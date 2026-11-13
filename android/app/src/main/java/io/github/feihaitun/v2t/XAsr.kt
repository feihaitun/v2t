package io.github.feihaitun.v2t

import android.content.Context
import com.k2fsa.sherpa.onnx.EndpointConfig
import com.k2fsa.sherpa.onnx.EndpointRule
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.OfflineTransducerModelConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineRecognizer
import com.k2fsa.sherpa.onnx.OnlineRecognizerConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import java.io.File
import kotlin.math.sqrt

/**
 * Chinese / English / mixed transcription with X-ASR zh-en through sherpa-onnx, in two passes:
 * the streaming model shows text while it is spoken, and when a sentence ends (a short pause,
 * or 20 s of continuous speech) the offline model re-decodes the whole segment, which is more accurate.
 *
 * Not thread-safe: [load], [accept] and [finish] run on one worker thread.
 */
class XAsr(private val directory: File) {
    companion object {
        const val STREAMING = "sherpa-onnx-x-asr-960ms-streaming-zipformer-transducer-zh-en-punct-int8-2026-06-05"
        const val OFFLINE = "sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03"
        const val SAMPLE_RATE = 16_000

        /** Pushed with android/push-models.sh or imported as a model ZIP; about 330 MB. */
        fun modelsDirectory(context: Context) = File(context.getExternalFilesDir(null), "models")

        /** Every file [load] opens; an interrupted copy leaves some missing. */
        fun installed(context: Context): Boolean {
            val dir = modelsDirectory(context)
            val streaming = listOf("encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx", "tokens.txt").map { "$STREAMING/$it" }
            val offline = listOf(
                "encoder-epoch-99-avg-1.int8.onnx", "decoder-epoch-99-avg-1.onnx", "joiner-epoch-99-avg-1.int8.onnx", "tokens.txt",
            ).map { "$OFFLINE/$it" }
            return (streaming + offline).all { File(dir, it).isFile }
        }

        /** Below this RMS the audio counts as a pause; the model's own endpointing reacts late. */
        private const val QUIET_LEVEL = 0.003f
        private const val PAUSE_SECONDS = 0.8
        /** Segments with less sound than this are pauses; decoding them invents filler words. */
        private const val MINIMUM_SOUND_SECONDS = 0.3
    }

    /** A line of text: [isFinal] false while it is still being heard. Times are seconds of audio heard. */
    data class Update(val text: String, val isFinal: Boolean, val start: Double, val end: Double)

    private var online: OnlineRecognizer? = null
    private var stream: OnlineStream? = null
    private var offline: OfflineRecognizer? = null

    private var segment = FloatArray(SAMPLE_RATE * 30)
    private var segmentLength = 0
    private var segmentStart = 0.0
    private var lastPartial = ""
    private var quietSeconds = 0.0
    private var soundSeconds = 0.0

    /** Loads both models: a second or two, so never on the main thread. */
    fun load() {
        val streaming = File(directory, STREAMING).path
        val final = File(directory, OFFLINE).path
        val features = FeatureConfig(sampleRate = SAMPLE_RATE, featureDim = 80)
        val recognizer = OnlineRecognizer(
            assetManager = null,
            config = OnlineRecognizerConfig(
                featConfig = features,
                modelConfig = OnlineModelConfig(
                    transducer = OnlineTransducerModelConfig(
                        encoder = "$streaming/encoder.int8.onnx",
                        decoder = "$streaming/decoder.onnx",
                        joiner = "$streaming/joiner.int8.onnx",
                    ),
                    tokens = "$streaming/tokens.txt",
                    numThreads = 2,
                ),
                endpointConfig = EndpointConfig(
                    rule1 = EndpointRule(false, 2.4f, 0f),   // nothing recognised yet
                    rule2 = EndpointRule(true, 0.8f, 0f),    // pause after speech ends a sentence
                    rule3 = EndpointRule(false, 0f, 20f),    // commit long monologues regularly
                ),
                enableEndpoint = true,
            ),
        )
        online = recognizer
        stream = recognizer.createStream()
        offline = OfflineRecognizer(
            assetManager = null,
            config = OfflineRecognizerConfig(
                featConfig = features,
                modelConfig = OfflineModelConfig(
                    transducer = OfflineTransducerModelConfig(
                        encoder = "$final/encoder-epoch-99-avg-1.int8.onnx",
                        decoder = "$final/decoder-epoch-99-avg-1.onnx",
                        joiner = "$final/joiner-epoch-99-avg-1.int8.onnx",
                    ),
                    tokens = "$final/tokens.txt",
                    numThreads = 4,
                ),
                // Greedy search drops repeated identical characters with this model.
                decodingMethod = "modified_beam_search",
            ),
        )
    }

    /** Feeds 16 kHz mono samples; returns what changed (a new partial, or a finished line). */
    fun accept(samples: FloatArray, count: Int = samples.size): List<Update> {
        val online = online ?: return emptyList()
        val stream = stream ?: return emptyList()
        val updates = mutableListOf<Update>()
        append(samples, count)
        val chunk = if (count == samples.size) samples else samples.copyOf(count)
        stream.acceptWaveform(chunk, SAMPLE_RATE)
        while (online.isReady(stream)) online.decode(stream)

        val partial = online.getResult(stream).text.trim()
        if (partial.isNotEmpty() && partial != lastPartial) {
            lastPartial = partial
            updates += update(partial, isFinal = false)
        }

        val duration = count.toDouble() / SAMPLE_RATE
        var sum = 0f
        for (i in 0 until count) sum += chunk[i] * chunk[i]
        if (sqrt(sum / count.coerceAtLeast(1)) < QUIET_LEVEL) {
            quietSeconds += duration
        } else {
            quietSeconds = 0.0
            soundSeconds += duration
        }
        val paused = quietSeconds >= PAUSE_SECONDS && hasWords(lastPartial)
        if (paused || online.isEndpoint(stream)) finishSegment()?.let { updates += it }
        return updates
    }

    /**
     * Stopping: decodes the last unfinished chunk too (the streaming model waits for a full
     * 960 ms otherwise) and returns the last sentence, if anything was said.
     */
    fun finish(): Update? {
        val online = online ?: return null
        val stream = stream ?: return null
        stream.inputFinished()
        while (online.isReady(stream)) online.decode(stream)
        lastPartial = online.getResult(stream).text.trim().ifEmpty { lastPartial }
        return finishSegment()
    }

    /**
     * Pausing: ends the sentence being heard, and starts a fresh stream so audio from before
     * the pause cannot leak into the first sentence after it.
     */
    fun pause(): Update? {
        val online = online ?: return null
        val stream = stream ?: return null
        while (online.isReady(stream)) online.decode(stream)
        lastPartial = online.getResult(stream).text.trim().ifEmpty { lastPartial }
        val update = finishSegment()
        stream.release()
        this.stream = online.createStream()
        return update
    }

    fun release() {
        stream?.release()
        online?.release()
        offline?.release()
        stream = null
        online = null
        offline = null
    }

    private fun append(samples: FloatArray, count: Int) {
        if (segmentLength + count > segment.size) segment = segment.copyOf((segmentLength + count) * 2)
        System.arraycopy(samples, 0, segment, segmentLength, count)
        segmentLength += count
    }

    private fun hasWords(text: String) = text.any { it.isLetter() }

    private fun finishSegment(): Update? {
        val online = online ?: return null
        val stream = stream ?: return null
        var result: Update? = null
        val offline = offline
        if (hasWords(lastPartial) && soundSeconds >= MINIMUM_SOUND_SECONDS && offline != null) {
            // A little trailing silence helps the last word come out. A new array is all zeros;
            // the reused segment buffer still holds older audio past segmentLength.
            val padded = FloatArray(segmentLength + SAMPLE_RATE / 2)
            System.arraycopy(segment, 0, padded, 0, segmentLength)
            val s = offline.createStream()
            s.acceptWaveform(padded, SAMPLE_RATE)
            offline.decode(s)
            val final = offline.getResult(s).text.trim()
            s.release()
            result = update(final.ifEmpty { lastPartial }, isFinal = true)
        }
        online.reset(stream)
        segmentStart += segmentLength.toDouble() / SAMPLE_RATE
        segmentLength = 0
        lastPartial = ""
        soundSeconds = 0.0
        return result
    }

    private fun update(text: String, isFinal: Boolean) =
        Update(text, isFinal, segmentStart, segmentStart + segmentLength.toDouble() / SAMPLE_RATE)
}

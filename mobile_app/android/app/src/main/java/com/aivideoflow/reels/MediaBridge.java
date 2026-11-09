package com.aivideoflow.reels;

import android.graphics.Rect;
import android.media.Image;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.media.MediaMuxer;

import java.io.IOException;
import java.nio.ByteBuffer;

/**
 * Video in/out for the Python engine on Android, where OpenCV is built without FFmpeg.
 * Frames cross to Python as packed I420 (Y plane, then U, then V); Python converts them with cv2.
 *   Reader: decodes a video file frame by frame (seekable).
 *   Writer: encodes I420 frames to an H.264 .mp4.
 *   muxAudio: copies the video and adds a music / reference audio track as AAC.
 */
public final class MediaBridge {
    private static final long TIMEOUT_US = 10_000;

    private MediaBridge() {}

    private static int findTrack(MediaExtractor ex, String prefix) {
        for (int i = 0; i < ex.getTrackCount(); i++) {
            String mime = ex.getTrackFormat(i).getString(MediaFormat.KEY_MIME);
            if (mime != null && mime.startsWith(prefix)) return i;
        }
        return -1;
    }

    // ------------------------------------------------------------------
    // Reader
    // ------------------------------------------------------------------
    public static final class Reader {
        private final MediaExtractor extractor = new MediaExtractor();
        private final MediaCodec decoder;
        private final MediaFormat format;
        private final MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        public final int width, height, rotation;
        public final long durationUs;
        public final double fps;
        private boolean inputDone, outputDone;
        private long lastPtsUs = -1;

        public Reader(String path) throws IOException {
            extractor.setDataSource(path);
            int track = findTrack(extractor, "video/");
            if (track < 0) throw new IOException("No video track in " + path);
            extractor.selectTrack(track);
            MediaFormat fmt = extractor.getTrackFormat(track);
            width = fmt.getInteger(MediaFormat.KEY_WIDTH);
            height = fmt.getInteger(MediaFormat.KEY_HEIGHT);
            rotation = fmt.containsKey("rotation-degrees") ? fmt.getInteger("rotation-degrees") : 0;
            durationUs = fmt.containsKey(MediaFormat.KEY_DURATION) ? fmt.getLong(MediaFormat.KEY_DURATION) : 0;
            fps = fmt.containsKey(MediaFormat.KEY_FRAME_RATE) ? fmt.getInteger(MediaFormat.KEY_FRAME_RATE) : 30.0;
            fmt.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible);
            format = fmt;
            decoder = MediaCodec.createDecoderByType(fmt.getString(MediaFormat.KEY_MIME));
            decoder.configure(fmt, null, null, 0);
            decoder.start();
        }

        public long lastPtsUs() { return lastPtsUs; }

        /** Jumps to the sync frame at or before `timeUs`; the next frames continue from there. */
        public void seek(long timeUs) {
            extractor.seekTo(timeUs, MediaExtractor.SEEK_TO_PREVIOUS_SYNC);
            if (inputDone) {
                // Some decoders produce nothing after flush() once end-of-stream was queued: restart instead
                decoder.stop();
                decoder.configure(format, null, null, 0);
                decoder.start();
            } else {
                decoder.flush();
            }
            inputDone = outputDone = false;
            lastPtsUs = -1;
        }

        /** Next decoded frame as packed I420 at the visible size, or null at the end of the video. */
        public byte[] next() {
            int idlePolls = 0;
            while (!outputDone) {
                if (!inputDone) {
                    int in = decoder.dequeueInputBuffer(TIMEOUT_US);
                    if (in >= 0) {
                        ByteBuffer buf = decoder.getInputBuffer(in);
                        int size = extractor.readSampleData(buf, 0);
                        if (size < 0) {
                            decoder.queueInputBuffer(in, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inputDone = true;
                        } else {
                            decoder.queueInputBuffer(in, 0, size, extractor.getSampleTime(), 0);
                            extractor.advance();
                        }
                    }
                }
                int out = decoder.dequeueOutputBuffer(info, TIMEOUT_US);
                if (out < 0) {
                    // Never wait forever: ~3 s without output once all input is in (10 s otherwise) counts as the end
                    if (++idlePolls > (inputDone ? 300 : 1000)) outputDone = true;
                    continue;
                }
                idlePolls = 0;
                if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) outputDone = true;
                if (info.size == 0) {
                    decoder.releaseOutputBuffer(out, false);
                    continue;
                }
                byte[] frame;
                try (Image img = decoder.getOutputImage(out)) {
                    frame = img == null ? null : toI420(img);
                }
                lastPtsUs = info.presentationTimeUs;
                decoder.releaseOutputBuffer(out, false);
                if (frame != null) return frame;
            }
            return null;
        }

        private int lastW, lastH;

        /** Size of the frame last returned by next() (even numbers, before rotation). */
        public int frameWidth() { return lastW; }
        public int frameHeight() { return lastH; }

        private byte[] toI420(Image img) {
            Rect crop = img.getCropRect();
            int w = crop.width() & ~1, h = crop.height() & ~1;
            lastW = w;
            lastH = h;
            byte[] out = new byte[w * h * 3 / 2];
            Image.Plane[] planes = img.getPlanes();
            int pos = 0;
            for (int p = 0; p < 3; p++) {
                int pw = p == 0 ? w : w / 2, ph = p == 0 ? h : h / 2;
                int cx = p == 0 ? crop.left : crop.left / 2, cy = p == 0 ? crop.top : crop.top / 2;
                ByteBuffer buf = planes[p].getBuffer();
                int rs = planes[p].getRowStride(), ps = planes[p].getPixelStride();
                byte[] row = new byte[rs];
                for (int y = 0; y < ph; y++) {
                    int start = (cy + y) * rs + cx * ps;
                    int len = Math.min(rs, buf.limit() - start);
                    buf.position(start);
                    buf.get(row, 0, Math.min(len, (pw - 1) * ps + 1));
                    if (ps == 1) {
                        System.arraycopy(row, 0, out, pos, pw);
                        pos += pw;
                    } else {
                        for (int x = 0; x < pw; x++) out[pos++] = row[x * ps];
                    }
                }
            }
            return out;
        }

        public void release() {
            try { decoder.stop(); } catch (Exception ignored) {}
            decoder.release();
            extractor.release();
        }
    }

    // ------------------------------------------------------------------
    // Writer
    // ------------------------------------------------------------------
    public static final class Writer {
        private final MediaCodec encoder;
        private final MediaMuxer muxer;
        private final MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        private final int width, height;
        private final double fps;
        private int track = -1;
        private long frames;

        public Writer(String path, int width, int height, double fps) throws IOException {
            this.width = width;
            this.height = height;
            this.fps = fps;
            MediaFormat fmt = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height);
            fmt.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible);
            fmt.setInteger(MediaFormat.KEY_BIT_RATE, 10_000_000);
            fmt.setInteger(MediaFormat.KEY_FRAME_RATE, (int) Math.round(fps));
            fmt.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);
            encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);
            encoder.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            encoder.start();
            muxer = new MediaMuxer(path, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
        }

        /** One frame of packed I420 at the writer's size. */
        public void write(byte[] i420) {
            int in;
            while ((in = encoder.dequeueInputBuffer(TIMEOUT_US)) < 0) drain(false);
            Image img = encoder.getInputImage(in);
            int pos = 0;
            Image.Plane[] planes = img.getPlanes();
            for (int p = 0; p < 3; p++) {
                int pw = p == 0 ? width : width / 2, ph = p == 0 ? height : height / 2;
                ByteBuffer buf = planes[p].getBuffer();
                int rs = planes[p].getRowStride(), ps = planes[p].getPixelStride();
                for (int y = 0; y < ph; y++) {
                    if (ps == 1) {
                        buf.position(y * rs);
                        buf.put(i420, pos, pw);
                        pos += pw;
                    } else {
                        int base = y * rs;
                        for (int x = 0; x < pw; x++) buf.put(base + x * ps, i420[pos++]);
                    }
                }
            }
            long pts = Math.round(frames * 1_000_000.0 / fps);
            encoder.queueInputBuffer(in, 0, width * height * 3 / 2, pts, 0);
            frames++;
            drain(false);
        }

        private void drain(boolean end) {
            while (true) {
                int out = encoder.dequeueOutputBuffer(info, end ? TIMEOUT_US : 0);
                if (out == MediaCodec.INFO_TRY_AGAIN_LATER) {
                    if (!end) return;
                    continue;
                }
                if (out == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    track = muxer.addTrack(encoder.getOutputFormat());
                    muxer.start();
                    continue;
                }
                if (out < 0) continue;
                ByteBuffer data = encoder.getOutputBuffer(out);
                if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) info.size = 0;
                if (info.size > 0 && track >= 0) muxer.writeSampleData(track, data, info);
                encoder.releaseOutputBuffer(out, false);
                if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) return;
            }
        }

        public void release() {
            int in;
            while ((in = encoder.dequeueInputBuffer(TIMEOUT_US)) < 0) drain(false);
            encoder.queueInputBuffer(in, 0, 0, Math.round(frames * 1_000_000.0 / fps), MediaCodec.BUFFER_FLAG_END_OF_STREAM);
            drain(true);
            encoder.stop();
            encoder.release();
            if (track >= 0) muxer.stop();
            muxer.release();
        }
    }

    // ------------------------------------------------------------------
    // Audio
    // ------------------------------------------------------------------
    /**
     * Writes `outPath`: the video track of `videoPath` plus the first audio track of `audioPath`
     * (an audio file or a video), cut to the video's length. AAC is copied; anything else is re-encoded to AAC.
     */
    public static void muxAudio(String videoPath, String audioPath, String outPath) throws IOException {
        MediaExtractor vEx = new MediaExtractor();
        MediaExtractor aEx = new MediaExtractor();
        MediaMuxer muxer = new MediaMuxer(outPath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
        try {
            vEx.setDataSource(videoPath);
            int vTrack = findTrack(vEx, "video/");
            vEx.selectTrack(vTrack);
            MediaFormat vFmt = vEx.getTrackFormat(vTrack);
            long videoUs = vFmt.containsKey(MediaFormat.KEY_DURATION) ? vFmt.getLong(MediaFormat.KEY_DURATION) : Long.MAX_VALUE;

            aEx.setDataSource(audioPath);
            int aTrack = findTrack(aEx, "audio/");
            if (aTrack < 0) throw new IOException("No audio track in " + audioPath);
            aEx.selectTrack(aTrack);
            MediaFormat aFmt = aEx.getTrackFormat(aTrack);
            boolean isAac = MediaFormat.MIMETYPE_AUDIO_AAC.equals(aFmt.getString(MediaFormat.KEY_MIME));

            // Non-AAC audio is converted first: the muxer needs every track's format before it starts
            java.util.List<Sample> aacSamples = isAac ? null : new java.util.ArrayList<>();
            MediaFormat outAFmt = isAac ? aFmt : toAac(aEx, aFmt, videoUs, aacSamples);
            int outV = muxer.addTrack(vFmt);
            int outA = muxer.addTrack(outAFmt);
            muxer.start();

            ByteBuffer buf = ByteBuffer.allocate(4 * 1024 * 1024);
            MediaCodec.BufferInfo bi = new MediaCodec.BufferInfo();
            while (true) {
                int size = vEx.readSampleData(buf, 0);
                if (size < 0) break;
                bi.set(0, size, vEx.getSampleTime(), vEx.getSampleFlags());
                muxer.writeSampleData(outV, buf, bi);
                vEx.advance();
            }
            if (isAac) {
                while (true) {
                    int size = aEx.readSampleData(buf, 0);
                    if (size < 0 || aEx.getSampleTime() > videoUs) break;
                    bi.set(0, size, aEx.getSampleTime(), 0);
                    muxer.writeSampleData(outA, buf, bi);
                    aEx.advance();
                }
            } else {
                for (Sample smp : aacSamples) {
                    bi.set(0, smp.data.length, smp.ptsUs, 0);
                    muxer.writeSampleData(outA, ByteBuffer.wrap(smp.data), bi);
                }
            }
            muxer.stop();
        } finally {
            muxer.release();
            vEx.release();
            aEx.release();
        }
    }

    private static final class Sample {
        final byte[] data;
        final long ptsUs;
        Sample(byte[] data, long ptsUs) { this.data = data; this.ptsUs = ptsUs; }
    }

    /** Decodes any audio track (mp3, opus, wav…) up to `maxUs` and encodes it to AAC; returns the AAC format. */
    private static MediaFormat toAac(MediaExtractor ex, MediaFormat src, long maxUs, java.util.List<Sample> samples) throws IOException {
        int sampleRate = src.getInteger(MediaFormat.KEY_SAMPLE_RATE);
        int srcChannels = src.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
        int channels = Math.min(2, srcChannels);
        MediaFormat fmt = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC, sampleRate, channels);
        fmt.setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC);
        fmt.setInteger(MediaFormat.KEY_BIT_RATE, 192_000);
        fmt.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 256 * 1024);
        MediaCodec encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC);
        encoder.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
        MediaCodec decoder = MediaCodec.createDecoderByType(src.getString(MediaFormat.KEY_MIME));
        decoder.configure(src, null, null, 0);
        encoder.start();
        decoder.start();

        MediaCodec.BufferInfo di = new MediaCodec.BufferInfo(), ei = new MediaCodec.BufferInfo();
        MediaFormat outFmt = null;
        boolean decInDone = false, decOutDone = false, encDone = false;
        byte[] pending = null;
        int pendingOff = 0;
        long pcmFrames = 0;
        try {
            while (!encDone) {
                // 1. Compressed audio -> decoder
                if (!decInDone) {
                    int in = decoder.dequeueInputBuffer(TIMEOUT_US);
                    if (in >= 0) {
                        int size = ex.readSampleData(decoder.getInputBuffer(in), 0);
                        if (size < 0 || ex.getSampleTime() > maxUs) {
                            decoder.queueInputBuffer(in, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            decInDone = true;
                        } else {
                            decoder.queueInputBuffer(in, 0, size, ex.getSampleTime(), 0);
                            ex.advance();
                        }
                    }
                }
                // 2. Decoder -> PCM waiting for the encoder
                if (pending == null && !decOutDone) {
                    int out = decoder.dequeueOutputBuffer(di, TIMEOUT_US);
                    if (out >= 0) {
                        ByteBuffer pcm = decoder.getOutputBuffer(out);
                        byte[] data = new byte[di.size];
                        pcm.position(di.offset);
                        pcm.get(data);
                        decoder.releaseOutputBuffer(out, false);
                        if (srcChannels > 2) data = downmix(data, srcChannels);
                        pending = data;
                        pendingOff = 0;
                        if ((di.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) decOutDone = true;
                    }
                }
                // 3. PCM -> encoder (end of stream once everything is in)
                if (pending != null) {
                    int in = encoder.dequeueInputBuffer(TIMEOUT_US);
                    if (in >= 0) {
                        ByteBuffer eb = encoder.getInputBuffer(in);
                        int n = Math.min(eb.remaining(), pending.length - pendingOff);
                        eb.put(pending, pendingOff, n);
                        long pts = pcmFrames * 1_000_000L / sampleRate;
                        pcmFrames += n / (2 * channels);
                        pendingOff += n;
                        boolean last = decOutDone && pendingOff >= pending.length;
                        encoder.queueInputBuffer(in, 0, n, pts, last ? MediaCodec.BUFFER_FLAG_END_OF_STREAM : 0);
                        if (pendingOff >= pending.length) pending = null;
                    }
                }
                // 4. Encoder -> AAC samples
                int out = encoder.dequeueOutputBuffer(ei, 0);
                if (out == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    outFmt = encoder.getOutputFormat();
                } else if (out >= 0) {
                    ByteBuffer data = encoder.getOutputBuffer(out);
                    if ((ei.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0 && ei.size > 0) {
                        byte[] b = new byte[ei.size];
                        data.position(ei.offset);
                        data.get(b);
                        samples.add(new Sample(b, ei.presentationTimeUs));
                    }
                    encoder.releaseOutputBuffer(out, false);
                    if ((ei.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) encDone = true;
                }
            }
        } finally {
            decoder.stop();
            decoder.release();
            encoder.stop();
            encoder.release();
        }
        if (outFmt == null) throw new IOException("Audio could not be converted to AAC.");
        return outFmt;
    }

    /** 16-bit PCM with more than two channels: keep the first two. */
    private static byte[] downmix(byte[] in, int ch) {
        int frames = in.length / (2 * ch);
        byte[] out = new byte[frames * 4];
        for (int f = 0; f < frames; f++) System.arraycopy(in, f * 2 * ch, out, f * 4, 4);
        return out;
    }
}

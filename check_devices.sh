#!/bin/bash
# Script to check available video and audio devices on Raspberry Pi

echo "=================================="
echo "🎥 VIDEO DEVICES"
echo "=================================="
ls -la /dev/video* 2>/dev/null || echo "❌ No video devices found"
echo ""

echo "=================================="
echo "📹 VIDEO DEVICE CAPABILITIES"
echo "=================================="
for dev in /dev/video*; do
    if [ -e "$dev" ]; then
        echo "Device: $dev"
        v4l2-ctl --device=$dev --all 2>&1 | grep -A5 "Driver Info\|Pixel Format\|Frame Size"
        echo "---"
    fi
done
echo ""

echo "=================================="
echo "🎤 AUDIO DEVICES (ALSA)"
echo "=================================="
arecord -l 2>/dev/null || echo "❌ No audio recording devices found"
echo ""

echo "=================================="
echo "🎵 DETAILED AUDIO INFO"
echo "=================================="
cat /proc/asound/cards 2>/dev/null || echo "❌ No sound cards found"
echo ""

echo "=================================="
echo "🔌 USB DEVICES"
echo "=================================="
lsusb
echo ""

echo "=================================="
echo "🎬 TEST CAMERA WITH FFMPEG"
echo "=================================="
for dev in /dev/video*; do
    if [ -e "$dev" ]; then
        echo "Testing $dev with FFmpeg..."
        timeout 2 ffmpeg -f v4l2 -list_formats all -i $dev 2>&1 | grep -E "Compressed|Raw"
        echo "---"
    fi
done
echo ""

echo "=================================="
echo "🎙️ TEST AUDIO WITH FFMPEG"
echo "=================================="
echo "Testing plughw:0,0..."
timeout 2 ffmpeg -f alsa -i plughw:0,0 -t 1 -f null - 2>&1 | tail -5
echo ""

echo "Testing plughw:1,0..."
timeout 2 ffmpeg -f alsa -i plughw:1,0 -t 1 -f null - 2>&1 | tail -5
echo ""

echo "=================================="
echo "✅ DEVICE CHECK COMPLETE"
echo "=================================="

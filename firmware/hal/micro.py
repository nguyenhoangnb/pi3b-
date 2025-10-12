import sounddevice as sd
import numpy as np
import wave


class Micro:
    def __init__(self, device=None, sample_rate=48000):
        """
        device: tên hoặc index thiết bị (vd: 'USB Audio Device' hoặc 2)
        """
        self.device = device
        self.sample_rate = sample_rate
        self.channels = 1
        self.recording = None

    def get_first_available_device(self):
        """Trả về tên thiết bị dạng 'hw:X,Y' nếu có."""
        devices = sd.query_devices()
        input_devices = [
            (i, d["name"]) for i, d in enumerate(devices) if d["max_input_channels"] > 0
        ]

        if not input_devices:
            print("❌ Không tìm thấy thiết bị micro nào.")
            return None

        print("🎧 Các thiết bị micro khả dụng:")
        for i, name in input_devices:
            print(f"  [{i}] {name}")

        # Lấy thiết bị đầu tiên
        index, name = input_devices[0]
        # Trích xuất chuỗi 'hw:X,Y' nếu có trong name
        if "hw:" in name:
            hw_name = name.split("hw:")[-1].split(")")[0]
            device_str = f"hw:{hw_name}"
        else:
            device_str = f"hw:{index},0"

        print(f"✅ Sử dụng thiết bị micro: [{index}] {name} ({device_str})")
        self.device = device_str
        return device_str

    def record(self, duration=5):
        """Ghi âm trong N giây từ thiết bị khả dụng."""
        dev = self.get_first_available_device()
        if dev is None:
            raise RuntimeError("Không có thiết bị micro khả dụng.")

        print(f"🎤 Đang ghi âm {duration}s từ thiết bị {dev[1]}...")
        self.recording = sd.rec(
            int(duration * self.sample_rate),
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            device=dev[0]
        )
        sd.wait()
        print("✅ Hoàn tất ghi âm.")
        return self.recording

    def save(self, path="output.wav"):
        """Lưu dữ liệu âm thanh ra file WAV."""
        if self.recording is None:
            raise RuntimeError("⚠️ Không có dữ liệu để lưu.")
        print(f"💾 Đang lưu vào {path} ...")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(self.recording.tobytes())
        print("✅ Lưu thành công.")


if __name__ == "__main__":
    mic = Micro()  # không cần truyền device, tự chọn thiết bị đầu tiên
    mic.record(3)
    mic.save("test.wav")

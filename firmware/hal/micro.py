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
        """Trả về thiết bị micro khả dụng đầu tiên (index, name) hoặc None nếu không có."""
        devices = sd.query_devices()
        input_devices = [(i, d["name"]) for i, d in enumerate(devices) if d["max_input_channels"] > 0]

        if not input_devices:
            print("❌ Không tìm thấy thiết bị micro nào trong hệ thống.")
            return None

        print("🎧 Các thiết bị micro khả dụng:")
        for i, name in input_devices:
            print(f"  [{i}] {name}")

        # Nếu người dùng chỉ định thiết bị — kiểm tra tồn tại
        if self.device is not None:
            for i, name in input_devices:
                if (isinstance(self.device, int) and i == self.device) or \
                   (isinstance(self.device, str) and self.device.lower() in name.lower()):
                    print(f"✅ Sử dụng thiết bị micro: [{i}] {name}")
                    return (i, name)
            print(f"⚠️ Không tìm thấy thiết bị '{self.device}', chuyển sang mặc định.")

        # Nếu không chỉ định hoặc không tìm thấy -> chọn thiết bị đầu tiên
        first_dev = input_devices[0]
        print(f"✅ Sử dụng thiết bị mặc định: [{first_dev[0]}] {first_dev[1]}")
        self.device = first_dev[0]
        return first_dev

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

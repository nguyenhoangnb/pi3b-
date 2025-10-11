import os
import time
import shutil
from glob import glob

class USBManager:
    def __init__(self, path="/media/ssd", min_free_percent=10, min_free_gb=1.0, camera_id=1):
        self.path = path
        self.min_free_percent = min_free_percent
        self.min_free_gb = min_free_gb
        self.camera_id = camera_id

    def is_available(self):
        return os.path.ismount(self.path) and os.access(self.path, os.W_OK)

    def wait_until_available(self):
        print("⚠️ USB bị tháo hoặc lỗi. Chờ gắn lại...")
        while not self.is_available():
            time.sleep(2)
        print("✅ USB đã gắn, tiếp tục ghi.")

    def get_free_space_percent(self):
        if not self.is_available():
            return 0.0
        usage = shutil.disk_usage(self.path)
        return round((usage.free / usage.total) * 100.0, 2)

    def get_free_space_gb(self):
        if not self.is_available():
            return 0.0
        usage = shutil.disk_usage(self.path)
        return round(usage.free / (1024**3), 2)

    def has_enough_space(self):
        free_percent = self.get_free_space_percent()
        free_gb = self.get_free_space_gb()
        if  free_gb < self.min_free_gb:
            print(f"⚠️ USB gần đầy ({free_gb} GB trống, {free_percent}%).")
            self.cleanup_old_files()
        return self.get_free_space_gb() >= self.min_free_gb

    def list_videos(self):
        return sorted(glob(os.path.join(self.path, "video_*.mp4")))

    def cleanup_old_files(self):
        """Xóa các video cũ nhất cho đến khi còn đủ dung lượng."""
        videos = self.list_videos()
        if not videos:
            print("❌ Không có video nào để xóa.")
            return
        while self.get_free_space_gb() < self.min_free_gb:
            oldest = videos.pop(0)
            try:
                os.remove(oldest)
                print(f"🗑️ Xóa video cũ: {os.path.basename(oldest)}")
            except Exception as e:
                print(f"⚠️ Không thể xóa {oldest}: {e}")
            if not videos:
                break

    def get_new_filename(self):
        t = time.strftime("%Y%m%d-%H%M%S")
        return os.path.join(self.path, f"{t}_cam{self.camera_id}.mp4")

    def factory_reset(self):
        """Xóa tất cả file ngoại trừ serial/license."""
        if not self.is_available():
            print("❌ USB chưa gắn, không thể reset.")
            return
        for f in os.listdir(self.path):
            fp = os.path.join(self.path, f)
            if f in ("serial.txt", "license.key"):
                continue
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
                elif os.path.isdir(fp):
                    shutil.rmtree(fp)
                print(f"🧹 Đã xóa: {f}")
            except Exception as e:
                print(f"⚠️ Lỗi khi xóa {f}: {e}")
        print("✅ Factory reset hoàn tất, giữ lại serial & license.")

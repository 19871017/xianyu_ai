import os
import asyncio
import aiohttp
from config import IMAGE_DIR
from utils.helpers import ensure_dir, sanitize_filename


class ImageDownloader:
    """异步批量图片下载器"""

    def __init__(self, save_dir: str = None, max_concurrent: int = 5):
        self.save_dir = save_dir or IMAGE_DIR
        self.max_concurrent = max_concurrent

    async def _download_one(self, session: aiohttp.ClientSession, url: str, save_path: str) -> bool:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    with open(save_path, "wb") as f:
                        f.write(await resp.read())
                    return True
        except Exception:
            pass
        return False

    async def _download_item_images(self, session: aiohttp.ClientSession, item: dict) -> dict:
        """下载单个商品的所有图片"""
        item_id = item.get("item_id", "unknown")
        image_url = item.get("image_url", "")

        if not image_url:
            item["local_images"] = []
            return item

        item_dir = os.path.join(self.save_dir, sanitize_filename(item_id))
        ensure_dir(item_dir)

        # 主图
        ext = ".jpg"
        if ".png" in image_url:
            ext = ".png"
        elif ".webp" in image_url:
            ext = ".webp"

        save_path = os.path.join(item_dir, f"main{ext}")
        success = await self._download_one(session, image_url, save_path)

        item["local_images"] = [save_path] if success else []
        item["image_dir"] = item_dir
        return item

    async def download_all(self, items: list) -> list:
        """批量下载所有商品图片"""
        ensure_dir(self.save_dir)
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async with aiohttp.ClientSession() as session:
            tasks = []
            for item in items:
                async def _task(it=item):
                    async with semaphore:
                        return await self._download_item_images(session, it)
                tasks.append(_task())

            results = await asyncio.gather(*tasks, return_exceptions=True)

        success_count = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                items[i]["local_images"] = []
            else:
                items[i] = result
                if items[i].get("local_images"):
                    success_count += 1

        print(f"图片下载完成: {success_count}/{len(items)}")
        return items

    def download_sync(self, items: list) -> list:
        """同步包装"""
        return asyncio.run(self.download_all(items))

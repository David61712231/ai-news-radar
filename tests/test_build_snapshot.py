import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "build_snapshot.py"
SPEC = importlib.util.spec_from_file_location("build_snapshot", MODULE_PATH)
BUILD_SNAPSHOT = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BUILD_SNAPSHOT)


class MergeWechatItemsTests(unittest.TestCase):
    def test_wechat_article_query_is_part_of_its_identity(self):
        first = "https://mp.weixin.qq.com/s?__biz=a&mid=1&idx=1"
        second = "https://mp.weixin.qq.com/s?__biz=a&mid=2&idx=1"

        self.assertNotEqual(BUILD_SNAPSHOT.norm_url(first), BUILD_SNAPSHOT.norm_url(second))

    def test_duplicate_keeps_tracked_account_attribution(self):
        existing = [{
            "id": "api-1",
            "title": "同一篇文章",
            "url": "https://mp.weixin.qq.com/s?article=1",
            "source": "AI HOT 来源",
        }]
        wechat = [{
            "id": "wechat:1",
            "title": "同一篇文章",
            "url": "https://mp.weixin.qq.com/s?article=1",
            "source": "公众号：白鲸出海",
            "mpName": "白鲸出海",
        }]

        items, merged, relabeled = BUILD_SNAPSHOT.merge_wechat_items(existing, wechat)

        self.assertEqual(len(items), 1)
        self.assertEqual(merged, 0)
        self.assertEqual(relabeled, 1)
        self.assertEqual(items[0]["source"], "公众号：白鲸出海")
        self.assertEqual(items[0]["sourceType"], "wechat")


if __name__ == "__main__":
    unittest.main()

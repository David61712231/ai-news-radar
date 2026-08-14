import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fetch_wechat.py"
SPEC = importlib.util.spec_from_file_location("fetch_wechat", MODULE_PATH)
FETCH_WECHAT = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(FETCH_WECHAT)


class FetchWechatTests(unittest.TestCase):
    def test_account_match_requires_an_explicit_exact_name(self):
        self.assertTrue(FETCH_WECHAT.is_exact_account_match("机器之心", "机器之心"))
        self.assertTrue(FETCH_WECHAT.is_exact_account_match("DeepTech 深科技", "DeepTech深科技"))
        self.assertFalse(FETCH_WECHAT.is_exact_account_match("机器之心", "机器之心Pro"))
        self.assertFalse(FETCH_WECHAT.is_exact_account_match("机器之心", "其他账号"))
        self.assertFalse(FETCH_WECHAT.is_exact_account_match("机器之心", ""))

    def test_relative_sogou_link_with_spaces_is_safely_requested(self):
        captured = {}

        def fake_http_get(url, cookie="", referer="", timeout=25):
            captured["url"] = url
            return "url += 'https://mp.weixin.qq.com/s?__biz=abc123';"

        with mock.patch.object(FETCH_WECHAT, "http_get", side_effect=fake_http_get):
            real_url, reason = FETCH_WECHAT.resolve_real_url(
                "/link?url=https%3A%2F%2Fmp.weixin.qq.com%2Fs%3Fid%3D1&query=Z Finance",
                cookie="",
            )

        self.assertEqual(reason, None)
        self.assertEqual(real_url, "https://mp.weixin.qq.com/s?__biz=abc123")
        self.assertEqual(
            captured["url"],
            "https://weixin.sogou.com/link?url=https%3A%2F%2Fmp.weixin.qq.com%2Fs%3Fid%3D1&query=Z%20Finance",
        )

    def test_resolve_failure_candidate_is_kept_without_redirect_url(self):
        fake_candidates = [{
            "title": "t1",
            "summary": "s1",
            "sogouLink": "/link?query=Z Finance",
            "source": "公众号：Z Finance",
            "publishedAt": "2026-01-01T00:00:00.000Z",
        }]
        with mock.patch.object(FETCH_WECHAT, "http_get", return_value="<html>ok</html>"), \
                mock.patch.object(FETCH_WECHAT, "parse_results", return_value=fake_candidates), \
                mock.patch.object(FETCH_WECHAT, "resolve_real_url", return_value=(None, "resolve_failed")), \
                mock.patch.object(FETCH_WECHAT.time, "sleep", return_value=None), \
                mock.patch.object(FETCH_WECHAT.random, "uniform", return_value=0.0):
            items, stats, failed_candidates = FETCH_WECHAT.fetch_all(["Z Finance"], "", per_account=5, days=7)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "")
        self.assertEqual(items[0]["originalUrlStatus"], "unresolved")
        self.assertEqual(stats["candidate_count"], 1)
        self.assertEqual(stats["resolved_count"], 0)
        self.assertEqual(stats["filtered_count"], 1)
        self.assertEqual(failed_candidates[0]["reason"], "resolve_failed")

    def test_main_writes_failure_summary_to_state(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            accounts = base / "accounts.json"
            out_json = base / "wechat_items.json"
            state_json = base / "wechat_state.json"
            accounts.write_text('{"accounts":[{"name":"A"}]}', encoding="utf-8")
            state_json.write_text('{"consecutive_failures":0}', encoding="utf-8")
            failed = (
                [{"account": "A", "title": f"t{i}", "reason": "resolve_failed"} for i in range(12)]
                + [{"account": "A", "title": f"a{i}", "reason": "antispider"} for i in range(7)]
                + [{"account": "A", "title": f"b{i}", "reason": "invalid_sogou_url"} for i in range(5)]
            )
            fake_stats = {
                "ok_accounts": 1,
                "fail_accounts": 0,
                "candidate_count": len(failed),
                "resolved_count": 0,
                "filtered_count": len(failed),
            }
            with mock.patch.object(FETCH_WECHAT, "build_session", return_value=None), \
                    mock.patch.object(FETCH_WECHAT, "fetch_all", return_value=([], fake_stats, failed)), \
                    mock.patch("sys.argv", ["fetch_wechat.py", "--accounts", str(accounts), "--out", str(out_json),
                                            "--state", str(state_json)]):
                rc = FETCH_WECHAT.main()

            self.assertEqual(rc, 0)
            state_data = json.loads(state_json.read_text(encoding="utf-8"))
            self.assertEqual(
                state_data["last_failure_reasons"],
                {"resolve_failed": 12, "antispider": 7, "invalid_sogou_url": 5},
            )
            self.assertEqual(len(state_data["last_failed_candidates"]), 20)
            self.assertEqual(
                sorted(state_data["last_failed_candidates"][0].keys()),
                ["account", "reason", "title"],
            )


if __name__ == "__main__":
    unittest.main()

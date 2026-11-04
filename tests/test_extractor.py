# -*- coding: utf-8 -*-
"""extractor 测试：截断、垃圾判定、分层、loop 去重"""
import pytest

from hippocampus import extractor


# ---------------------------------------------------------------- clip_content
def test_clip_content_truncates_3000_chars():
    """3000 字必须被截断，并以省略号收尾"""
    out = extractor.clip_content("字" * 3000)
    assert out.endswith("…")
    assert len(out) <= extractor.MAX_CONTENT


def test_clip_content_never_exceeds_limit():
    """严格契约：截断结果长度不超过 limit"""
    out = extractor.clip_content("字" * 3000)
    assert len(out) <= extractor.MAX_CONTENT


def test_clip_content_truncates_on_punctuation():
    """有标点时尽量断在句号处，而不是硬切"""
    out = extractor.clip_content("这是一句话。" * 500)
    assert out.endswith("…")
    assert out.startswith("这是一句话。")
    assert len(out) <= extractor.MAX_CONTENT + 1


def test_clip_content_keeps_short_text():
    text = "今天下午三点开会"
    assert extractor.clip_content(text) == text


def test_clip_content_custom_limit():
    out = extractor.clip_content("啊" * 200, limit=50)
    assert out.endswith("…")
    assert len(out) <= 51


def test_clip_content_none_and_blank():
    assert extractor.clip_content(None) == ""
    assert extractor.clip_content("   ") == ""


# ---------------------------------------------------------------- looks_like_junk
def test_looks_like_junk_repeated_chars():
    """单字符刷屏应判为垃圾"""
    assert extractor.looks_like_junk("啊啊啊啊啊啊") is True


def test_looks_like_junk_repeated_chars_long():
    """足够长的重复刷屏能被拦住（现有阈值的真实生效区间）"""
    assert extractor.looks_like_junk("啊" * 20) is True


def test_looks_like_junk_normal_sentence():
    """正常中文句子不是垃圾"""
    assert extractor.looks_like_junk("今天下午三点开会") is False


def test_looks_like_junk_pure_symbols():
    assert extractor.looks_like_junk("。。。") is True


def test_looks_like_junk_too_short():
    assert extractor.looks_like_junk("嗯") is True
    assert extractor.looks_like_junk(None) is True


# ---------------------------------------------------------------- detect_noise
def test_detect_noise_short_chitchat():
    assert extractor.detect_noise("嗯") is True
    assert extractor.detect_noise("好的") is True


def test_detect_noise_short_without_action_word():
    """10 字以内、又没有疑问/动作词的，当闲聊"""
    assert extractor.detect_noise("帮我看看这个文件") is True


def test_detect_noise_real_sentence():
    assert extractor.detect_noise("我明天还得看下报表，先这样吧") is False


# ---------------------------------------------------------------- extract
def test_extract_pact_is_L5_protected():
    """「以后别叫我老师，叫我小满就行」→ L5 + protected"""
    r = extractor.extract("以后别叫我老师，叫我小满就行")
    assert r["layer"] == "L5"
    assert r["protected"] is True
    assert r["keep"] is True
    assert r["noise"] is False
    assert r["content"] == "以后别叫我老师，叫我小满就行"


def test_extract_pact_only_for_user_role():
    """称呼约定只认用户说的，助手自己说的不算人格约定"""
    r = extractor.extract("以后别叫我老师，叫我小满就行", role="assistant")
    assert r["protected"] is False
    assert r["keep"] is True


def test_extract_chitchat_has_no_loops():
    """「帮我看看这个文件」不应该产生未闭合事项"""
    r = extractor.extract("帮我看看这个文件")
    assert r["loops"] == []
    assert r["keep"] is True


def test_extract_single_loop_not_duplicated():
    """一句话里同时有 promise 和 paused 信号时，只取优先级最高的 1 条"""
    r = extractor.extract("我明天还得看下报表，先这样吧")
    assert len(r["loops"]) == 1
    kind, title = r["loops"][0]
    assert kind == "promise"
    assert title


def test_extract_failed_gives_loop():
    r = extractor.extract("这个服务还是不行，起不来，我再看看别的办法")
    kinds = [k for k, _ in r["loops"]]
    assert kinds == ["failed"]


def test_extract_empty_text():
    r = extractor.extract("")
    assert r["keep"] is False
    assert r["loops"] == []
    assert r["layer"] == "L1"


def test_extract_preference_layer():
    """偏好类表达进 L2，且不保护"""
    r = extractor.extract("我习惯用命令行，不喜欢图形界面")
    assert r["layer"] == "L2"
    assert r["protected"] is False


def test_extract_entities():
    """路径与域名能被抽出来"""
    ents = extractor.extract_entities(r"项目在 D:\work\tan，域名是 tan.io，库用 SQLite")
    assert r"D:\work\tan" in ents
    assert "tan.io" in ents


# ---------------------------------------------------------------- summarize / 情绪
def test_summarize_short_text():
    assert extractor.summarize("今天下午三点开会") == "今天下午三点开会"


def test_summarize_truncates_long_text():
    s = extractor.summarize("这是一句很长的话" * 20, 30)
    assert len(s) <= 31
    assert s.endswith("…")


def test_valence_and_emotion():
    assert extractor.valence_of("太累了，不想干了") < 0
    assert extractor.valence_of("太好了，搞定了") > 0
    assert extractor.valence_of("今天下午三点开会") == 0.0
    assert extractor.emotion_of("太累了，不想干了") > 1.0

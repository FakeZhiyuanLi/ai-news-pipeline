"""The allowlist must deny by default and resist hostname spoofing."""

import sources


def test_allowlisted_domains_pass():
    assert sources.is_allowed("https://huggingface.co/blog/some-post")
    assert sources.is_allowed("https://openai.com/news/")
    assert sources.is_allowed("https://www.theverge.com/ai/story")  # true subdomain


def test_subdomain_spoofing_rejected():
    # evil-huggingface.co must NOT match huggingface.co
    assert not sources.is_allowed("https://evil-huggingface.co/blog")
    assert not sources.is_allowed("https://notopenai.com/news")
    # allowlisted domain as a subdomain of an attacker's host
    assert not sources.is_allowed("https://huggingface.co.evil.example/x")
    # allowlisted domain in the userinfo section
    assert not sources.is_allowed("https://huggingface.co@evil.example/x")


def test_deny_by_default():
    assert not sources.is_allowed("https://example.com/anything")
    assert not sources.is_allowed("ftp://huggingface.co/file")
    assert not sources.is_allowed("not a url")
    assert not sources.is_allowed("")


def test_trailing_dot_and_case_normalised():
    assert sources.is_allowed("https://HuggingFace.CO/blog")
    assert sources.is_allowed("https://huggingface.co./blog")


def test_every_feed_url_passes_its_own_allowlist():
    for entry in sources.all_sources():
        assert sources.is_allowed(entry["feed_url"]), entry["domain"]

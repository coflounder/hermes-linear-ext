import hashlib, hmac, sys, time, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "linear"))
import signature, oauth  # noqa: E402


def test_verify_signature_roundtrip(tmp_path=None):
    body = b'{"action":"create","type":"Comment"}'
    secret = "shhh"
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert signature.verify_signature(body, good, secret) is True
    assert signature.verify_signature(body, "sha256=" + good, secret) is True
    assert signature.verify_signature(body, good, "wrong") is False
    assert signature.verify_signature(body, "deadbeef", secret) is False
    assert signature.verify_signature(body, None, secret) is False
    assert signature.verify_signature(body, good, "") is False


def test_replay_window():
    now = 1_000_000_000_000
    assert signature.within_replay_window(now, now) is True
    assert signature.within_replay_window(now - 59_000, now) is True
    assert signature.within_replay_window(now - 61_000, now) is False
    assert signature.within_replay_window(None, now) is False


def test_authorize_url_has_actor_app_and_redirect():
    url = oauth.build_authorize_url("cid", "https://h.example.net", "STATE")
    assert "actor=app" in url
    assert url.startswith("https://linear.app/oauth/authorize?")
    assert "redirect_uri=https%3A%2F%2Fh.example.net%2Foauth%2Flinear%2Fcallback" in url
    assert "state=STATE" in url
    assert "app%3Amentionable" in url  # scope present, comma-joined+encoded


def test_token_store_roundtrip_and_expiry(tmp_path):
    clock = {"t": 1000}
    st = oauth.TokenStore(tmp_path / "tok.json", now=lambda: clock["t"])
    st.save({"access_token": "abc", "expires_in": 3600})
    assert st.access_token() == "abc"
    assert oct(os.stat(tmp_path / "tok.json").st_mode)[-3:] == "600"
    assert st.is_expired() is False
    clock["t"] = 1000 + 3600  # now at expiry
    assert st.is_expired() is True
    # non-expiring token
    st.save({"access_token": "perm"})
    assert st.is_expired() is False


def test_state_unique():
    assert oauth.gen_state() != oauth.gen_state()

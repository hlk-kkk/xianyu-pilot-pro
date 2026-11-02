from app.core import xianyu_qr_login


def test_allowed_login_redirect_hosts() -> None:
    assert xianyu_qr_login._is_allowed_login_url("https://www.goofish.com/")
    assert xianyu_qr_login._is_allowed_login_url("https://seller.goofish.com/")
    assert xianyu_qr_login._is_allowed_login_url("https://login.taobao.com/member/login.jhtml")
    assert xianyu_qr_login._is_allowed_login_url("https://passport.alibaba.com/")
    assert not xianyu_qr_login._is_allowed_login_url("https://example.com/")
    assert not xianyu_qr_login._is_allowed_login_url("https://goofish.com.evil.example/")
    assert not xianyu_qr_login._is_allowed_login_url("not-a-url")


def test_missing_critical_login_cookies() -> None:
    missing = xianyu_qr_login._missing_critical_login_cookies(
        {"cookie2": "v", "unb": "v", "m_h5_tk": "v"}
    )
    assert missing == ["_hvn_lgc_", "havana_lgc_exp"]

    complete = xianyu_qr_login._missing_critical_login_cookies(
        {
            "_hvn_lgc_": "v",
            "havana_lgc_exp": "v",
            "cookie2": "v",
            "unb": "v",
            "m_h5_tk": "v",
        }
    )
    assert complete == []


def test_parse_cookie_text_keeps_equals_in_value() -> None:
    cookies = xianyu_qr_login._parse_cookie_text(
        "a=1; b=x=y; broken; =empty; c="
    )
    assert cookies == {"a": "1", "b": "x=y", "c": ""}


def test_browser_session_status_pending_and_confirmed() -> None:
    pending = xianyu_qr_login._browser_session_status({"status": "pending"})
    assert pending["status"] == "pending"

    confirmed = xianyu_qr_login._browser_session_status(
        {"status": "confirmed", "saved_cookies": {"unb": "2980835701"}}
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["unb"] == "2980835701"
    assert confirmed["externalUid"] == "2980835701"

    expired = xianyu_qr_login._browser_session_status(
        {"status": "expired", "message": "二维码已过期，请重新生成"}
    )
    assert expired == {"status": "expired", "message": "二维码已过期，请重新生成"}

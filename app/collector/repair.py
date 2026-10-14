"""Fail-closed verification of replacement login and displayed account identity."""
from .browser import open_context, looks_like_login, visible_text


def verify_candidate(platform, path, expected_name):
    from .xhs import XHS_PRO_HOME_URL, XHS_SELECT_ACCOUNT_URL_MARKER, XHS_WRONG_ACCOUNT_MARKER
    from .pugongying import PGY_HOME_URL, PGY_ROLE_SELECT_URL_MARKER
    from .channels import CHANNELS_HOME_URL
    url = {"xhs": XHS_PRO_HOME_URL, "pugongying": PGY_HOME_URL, "channels": CHANNELS_HOME_URL}[platform]
    with open_context(path, headless=True) as page:
        page.goto(url, wait_until="domcontentloaded")
        # Portal account names load asynchronously. Require the exact expected
        # name in a visible text node, twice, not a substring or a supplied claim.
        clean = 0
        for _ in range(10):
            page.wait_for_timeout(1000)
            text = visible_text(page)
            if looks_like_login(page.url, text):
                clean = 0
                continue
            if XHS_SELECT_ACCOUNT_URL_MARKER in page.url or PGY_ROLE_SELECT_URL_MARKER in page.url:
                clean = 0
                continue
            if platform == "xhs" and XHS_WRONG_ACCOUNT_MARKER in text:
                return False
            names = page.get_by_text(expected_name, exact=True)
            matched = any(names.nth(i).is_visible() for i in range(names.count()))
            clean = clean + 1 if matched else 0
            if clean >= 2:
                return True
        return False

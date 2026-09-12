"""The action link leaves on our domain, not on Firebase's.

The console setting that would do this is refused on the project
(EMAIL_TEMPLATE_UPDATE_NOT_ALLOWED), so the rewrite happens where the link is
minted. What must stay true: the oobCode survives untouched, and a link that is
not on Firebase's default domain is never rewritten.
"""

from app.platform.security.firebase_admin import on_our_domain

MINTED = (
    "https://dop-qa.firebaseapp.com/__/auth/action"
    "?mode=verifyEmail&oobCode=lmz5uHW_wcgE&apiKey=AIza-test&lang=en"
)


def test_the_host_changes_and_nothing_else_does():
    out = on_our_domain(MINTED, "auth.qa.dop-t.com")

    assert out == (
        "https://auth.qa.dop-t.com/__/auth/action"
        "?mode=verifyEmail&oobCode=lmz5uHW_wcgE&apiKey=AIza-test&lang=en"
    )


def test_no_domain_configured_leaves_the_link_alone():
    assert on_our_domain(MINTED, "") == MINTED


def test_a_link_not_on_firebases_domain_is_never_rewritten():
    """The emulator mints on localhost; a future custom domain mints on itself.
    Rewriting those would point a real person at a host that serves nothing."""
    emulator = "http://localhost:9099/emulator/action?mode=verifyEmail&oobCode=x"

    assert on_our_domain(emulator, "auth.qa.dop-t.com") == emulator

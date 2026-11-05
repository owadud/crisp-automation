"""Mock Crisp conversation pages, using the real class names captured from Crisp
with tools/crisp_status_probe.py (2026-11-05). No real customer data."""

BASE = "https://app.crisp.chat/website/w-1/inbox/"


def _message(direction, when, prefix, body="Hello", marker="", marker_kind="", kind="text", event=""):
    status = ""
    if marker:
        status = (f'<div class="c-conversation-box-content-message__status"><div class="c-conversation-box-content-message__meta">'
                  f'<svg class="c-base-icon c-conversation-box-content-message__icon c-conversation-box-content-message__icon--{marker_kind}">'
                  f'<use xlink:href="/s.svg#icon-message_{marker_kind}"></use></svg>'
                  f'<div class="c-conversation-box-content-message__label">{marker}</div></div></div>')
    if kind == "event":
        tip = f'<span class="c-base-tooltip__default"><span>{when}</span></span>'
        content = (f'<span class="c-conversation-box-content-message-bubble__content u-semibold" data-event="{event}">'
                   f'<span>Resolved conversation</span></span>')
    else:
        main = f"<span>{prefix}</span><span>, </span><span>{when}</span>" if when else f"<span>{prefix}</span>"
        tip = (f'<span class="c-base-tooltip__default"></span><span class="c-base-tooltip__main u-semibold">{main}</span>'
               f'<span class="c-base-tooltip__sub u-regular"><span class="c-conversation-box-content-message-bubble__sub">'
               f'<span>Sent from Crisp</span></span></span>')
        content = f'<span class="c-conversation-box-content-message-bubble__content">{body}</span>'
    return (f'<div class="c-conversation-box-content-message c-conversation-box-content-message--{direction} '
            f'c-conversation-box-content-message--{kind}" data-message-fingerprint="123">'
            f'<div class="c-conversation-box-content-message__container"><div class="c-conversation-box-content-message-bubble">'
            f'<div class="c-base-tooltip-root c-conversation-box-content-message-bubble__tooltip">'
            f'<div class="c-conversation-box-content-message-bubble__tooltip c-base-tooltip"><span class="c-base-tooltip__value">'
            f'{tip}</span></div></div>{content}</div></div>{status}</div>')


def page(*messages) -> str:
    return ('<!doctype html><html><body><div class="c-conversation-box-content">'
            + "".join(messages) + "</div></body></html>")


def ours(when="9h (3:40 AM)", marker="", marker_kind=""):
    return _message("operator", when, "Sent", "Our offer for customer@example.com", marker, marker_kind)


def theirs(when="45m"):
    return _message("user", when, "Received", "Thanks, a question")


def resolved(when="9h (3:41 AM)"):
    return _message("operator", when, "", kind="event", event="state:resolved")


SCENARIOS = {
    "sent": page(ours()),
    "delivered": page(ours(marker="Delivered to email", marker_kind="delivered")),
    "read": page(ours(marker="Read in email", marker_kind="read")),
    "replied": page(ours(marker="Read in email", marker_kind="read"), resolved(), theirs("45m")),
    "replied_resolved": page(ours(), theirs("Yesterday (9:02 PM)"), resolved("30m")),
    "failed": page(ours(marker="Not delivered: mailbox full", marker_kind="failed")),
    "no_time": page(_message("operator", "", "Sent", "x", "Delivered to email", "delivered")),
    "layout": '<!doctype html><html><body><div class="totally-new-crisp">new design</div></body></html>',
    "login": ('<!doctype html><html><body><script>history.replaceState(null, "", "/initiate/login/");</script>'
              '<form><input type="email"><input type="password"></form></body></html>'),
}


def link(name: str) -> str:
    return f"{BASE}session_{name}-0000-1111/"

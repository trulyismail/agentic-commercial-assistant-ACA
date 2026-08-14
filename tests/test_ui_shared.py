"""
Tests des aides UI partagées (§18, `aca/ui/shared.py`).

Portée volontairement étroite : `_totp_qr_png()` est la seule fonction de ce module qui n'a besoin
d'aucun `st.session_state`/widget Streamlit pour être appelée directement — le reste (`check_auth`,
`_handle_totp_step`, `advance_graph`…) est déjà vérifié bout en bout via `AppTest`, la stratégie
établie pour `ui.py`/`app_pages/*.py` dans ce projet (cf. CLAUDE.md).
"""
from aca.ui import shared


def test_totp_qr_png_returns_a_real_png():
    png = shared._totp_qr_png("otpauth://totp/Acme:admin%40acme.fr?secret=JBSWY3DPEHPK3PXP&issuer=Acme")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 100  # un PNG vide/cassé ferait quelques dizaines d'octets, pas plus


def test_totp_qr_png_differs_for_different_secrets():
    png_a = shared._totp_qr_png("otpauth://totp/x?secret=AAAAAAAAAAAAAAAA")
    png_b = shared._totp_qr_png("otpauth://totp/x?secret=BBBBBBBBBBBBBBBB")
    assert png_a != png_b

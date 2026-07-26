from gui_agent.onboarding.url_resolution import resolve_public_url, strip_tracking_parameters


def test_tracking_cleanup_is_generic_and_preserves_functional_parameters() -> None:
    result = strip_tracking_parameters(
        "https://93.184.216.34/products?id=42&utm_source=search&gclid=abc#details"
    )

    assert result == "https://93.184.216.34/products?id=42"


def test_public_redirect_resolution_follows_hops_without_site_rules() -> None:
    responses = {
        "https://93.184.216.34/go": (302, "https://93.184.216.35/store?utm_campaign=sale&id=7"),
        "https://93.184.216.35/store?id=7": (200, None),
    }

    result = resolve_public_url(
        "https://93.184.216.34/go?utm_source=search",
        requester=lambda url: responses[url],
    )

    assert result["url"] == "https://93.184.216.35/store?id=7"
    assert result["changed"] is True
    assert result["redirectChain"] == [
        "https://93.184.216.34/go",
        "https://93.184.216.35/store?id=7",
    ]

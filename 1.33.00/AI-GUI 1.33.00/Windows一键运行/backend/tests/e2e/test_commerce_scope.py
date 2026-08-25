import pytest
from playwright.sync_api import sync_playwright

from gui_agent.domain.models import Step
from gui_agent.locating.strategies import LocatorError, resolve_step_locator


def _step(anchor: str, max_scroll_attempts: int = 0) -> Step:
    return Step(
        action="click",
        locator={"role": "button", "name": "Add to cart"},
        commerceScope={
            "kind": "product_card",
            "container": {"css": "[data-product-card]"},
            "anchor": {"text": anchor},
            "excludedMarkers": [{"css": "[data-ad]"}],
            "maxScrollAttempts": max_scroll_attempts,
        },
    )


@pytest.mark.e2e
def test_commerce_scope_selects_unique_non_ad_card_and_rejects_duplicates() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
          <article data-product-card data-id="a"><h2>E2E Product A</h2><button>Add to cart</button></article>
          <article data-product-card data-id="b"><h2>E2E Product B</h2><button>Add to cart</button></article>
          <article data-product-card data-id="ad"><i data-ad>Ad</i><h2>E2E Product A</h2><button>Add to cart</button></article>
        """)
        target = resolve_step_locator(page, _step("E2E Product A"))
        assert target.evaluate("element => element.closest('[data-product-card]').dataset.id") == "a"

        page.locator("[data-id=b] h2").evaluate("element => element.textContent = 'E2E Product A'")
        with pytest.raises(LocatorError, match="拒绝猜测"):
            resolve_step_locator(page, _step("E2E Product A"))
        browser.close()


@pytest.mark.e2e
def test_commerce_scope_retries_bounded_scroll_for_lazy_list() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
          <main id="list" style="height:2000px"></main>
          <script>
            let loaded = false;
            addEventListener('wheel', () => {
              if (loaded) return;
              loaded = true;
              document.querySelector('#list').insertAdjacentHTML('beforeend',
                '<article data-product-card><h2>Lazy E2E Product</h2><button>Add to cart</button></article>');
            });
          </script>
        """)
        target = resolve_step_locator(page, _step("Lazy E2E Product", max_scroll_attempts=2))
        assert target.count() == 1
        browser.close()

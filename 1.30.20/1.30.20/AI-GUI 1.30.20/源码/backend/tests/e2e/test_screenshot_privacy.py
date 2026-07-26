from playwright.sync_api import sync_playwright

from gui_agent.security.screenshot_privacy import screenshot_privacy_masks


def test_screenshot_privacy_masks_default_and_custom_regions_at_pixel_level() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 240, "height": 160})
        page.set_content("""
            <style>
              body { margin: 0; background: white; }
              #password { position: fixed; left: 10px; top: 10px; width: 100px; height: 30px; background: red; }
              .account { position: fixed; left: 10px; top: 60px; width: 100px; height: 30px; background: lime; }
            </style>
            <input id="password" type="password" value="private-value">
            <div class="account">Customer 1001</div>
        """)
        session = page.context.new_cdp_session(page)

        with screenshot_privacy_masks(page, (".account",)) as result:
            assert result["masked_count"] == 2
            assert page.locator("[data-gui-agent-privacy-mask]").count() == 2
            screenshot = session.send("Page.captureScreenshot", {"format": "png"})["data"]

        assert page.locator("[data-gui-agent-privacy-mask]").count() == 0
        pixel_page = browser.new_page()
        pixels = pixel_page.evaluate("""
            async (data) => {
              const image = new Image();
              image.src = `data:image/png;base64,${data}`;
              await image.decode();
              const canvas = document.createElement('canvas');
              canvas.width = image.width; canvas.height = image.height;
              const context = canvas.getContext('2d');
              context.drawImage(image, 0, 0);
              return {
                password: [...context.getImageData(20, 20, 1, 1).data],
                account: [...context.getImageData(20, 70, 1, 1).data],
                public: [...context.getImageData(180, 120, 1, 1).data]
              };
            }
        """, screenshot)
        assert pixels["password"] == [17, 17, 17, 255]
        assert pixels["account"] == [17, 17, 17, 255]
        assert pixels["public"] == [255, 255, 255, 255]
        browser.close()


def test_screenshot_privacy_ignores_invalid_selectors_without_leaving_masks() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<input type="password" value="private-value">')

        with screenshot_privacy_masks(page, (":not(",)) as result:
            assert result["masked_count"] == 1
            assert result["invalid_selectors"] == [":not("]

        assert page.locator("[data-gui-agent-privacy-mask]").count() == 0
        browser.close()

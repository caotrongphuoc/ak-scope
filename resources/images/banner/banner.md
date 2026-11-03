# ak-scope 2:1 banners

- [Animated GIF](banner-ak-scope.gif): the README banner.
- [Static PNG cover](banner-ak-scope-2x1.png): 1280×640 (2:1), suitable for sharing or uploading as
  a repository social preview. Clicking the README banner opens it; adding this file does not
  change GitHub repository settings.

The banner is generated documentation art, not a screenshot of the page. It follows the layout of
the other AK Base Kit repositories (Zomwar, Eleven Meter, Mini Clock, BLE Mesh Tracking): a 2:1
canvas, a large title, a preview panel and a row of stages, with the dark colors of the ak-scope
page. No artwork from those projects is copied.

- The title and captions use Inter, the font of the page (`akscope/web/fonts/`).
- The preview panel is the boot of the base kit application drawn from
  [`samples/base-kit-boot-akt.bin`](../../../samples/base-kit-boot-akt.bin): the idle task posts
  to the RF24 and UART interfaces, then the init chain UART_IF → LINK → LINK_MAC → LINK_PHY →
  LINK_MAC → LINK, 31.8 to 34.4 ms after INFO. Blocks are handler runs, arrows go from a post to
  the run it caused, the colors are the task colors of the page.
- The moving playhead and the blinking REC dot stand for the live view following a capture.

Made by [`tools/banner.py`](../../../tools/banner.py), which draws the page, shoots it in headless
Firefox and writes both files. The GIF has 24 frames of 120 ms (2.9 s loop) and a 255-colour
palette that keeps the task colors exact.

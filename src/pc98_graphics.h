#ifndef PC98QR_GRAPHICS_H
#define PC98QR_GRAPHICS_H

#define SCREEN_WIDTH 640
#define SCREEN_HEIGHT 400

/*
 * 通常のPC-98 DOSテキストコンソールを前提とし、640×400・16色GVRAMが必要。
 */
/* Normal PC-98 DOS text console; 640x400, 16-color GVRAM is required. */
void graphics_init(void);
void screen_clear(void); /* White, palette index 15. */ /* パレット番号15の白。 */
/*
 * 画面内に収まる黒い長方形を描く。
 * 幅と高さの単位は画素。
 */
/* Black rectangle, entirely within the screen; width/height are pixel counts. */
void box_fill(unsigned int x, unsigned int y,
              unsigned int width, unsigned int height);
void graphics_show(void);
void wait_key(void);
void graphics_shutdown(void);

#endif

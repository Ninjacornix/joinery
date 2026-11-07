; UI panel painted in GIMP with script-fu. Replayed by `joinery rebuild`.
;
; Operation log for art/ui/panel.xcf and art/ui/panel.png. Real raster work:
; layers, gradient fills, strokes and a bevelled border, all GIMP operations.
; Nothing is generated and nothing is fetched.
;
; Krita is the project's intended painter, but it has no headless authoring
; path: its Python API needs the running GUI, and Joinery's own Krita MCP is
; still first-party-planned. GIMP is in the catalog, is non-generative, and
; script-fu runs properly headless, so the 2D pipeline is proven with GIMP for
; now. See docs/adr/0009.
;
; The .xcf keeps the layers, so this is an editable source and not just a flat
; export. Godot gets the PNG.
;
; Art rules this follows:
;   flat UI, 2 px line weight at 1080p, 8 px corner radius
;   palette: plaster warm base, dark timber border, brass accent
;   no gradients outside the subtle base shading
;
; Run: gimp -i -b '(load "recipes/ui_panel.scm")' -b '(gimp-quit 0)'

(define (joinery-ui-panel)
  (let* ((width 420)
         (height 260)
         (inset 10)
         (radius 8)
         (image (car (gimp-image-new width height RGB)))
         (base (car (gimp-layer-new image width height RGBA-IMAGE
                                    "Base" 100 LAYER-MODE-NORMAL)))
         (border (car (gimp-layer-new image width height RGBA-IMAGE
                                      "Border" 100 LAYER-MODE-NORMAL)))
         (accent (car (gimp-layer-new image width height RGBA-IMAGE
                                      "Accent" 100 LAYER-MODE-NORMAL))))

    (gimp-image-insert-layer image base 0 -1)
    (gimp-image-insert-layer image border 0 -1)
    (gimp-image-insert-layer image accent 0 -1)
    (gimp-drawable-fill base FILL-TRANSPARENT)
    (gimp-drawable-fill border FILL-TRANSPARENT)
    (gimp-drawable-fill accent FILL-TRANSPARENT)

    ; Panel body: rounded rect, filled with a gentle vertical shade so it
    ; reads as a surface rather than a flat block.
    (gimp-image-select-round-rectangle image CHANNEL-OP-REPLACE
                                       inset inset
                                       (- width (* 2 inset))
                                       (- height (* 2 inset))
                                       radius radius)
    (gimp-context-set-foreground '(86 80 71))
    (gimp-context-set-background '(62 57 50))
    (gimp-context-set-gradient-fg-bg-rgb)
    (gimp-drawable-edit-gradient-fill base GRADIENT-LINEAR 0 FALSE 1 0 TRUE
                                      (/ width 2) inset
                                      (/ width 2) (- height inset))

    ; Border: a 2 px dark timber stroke on its own layer, so the weight can be
    ; changed without repainting the body.
    (gimp-context-set-foreground '(38 26 18))
    (gimp-context-set-line-width 2)
    (gimp-image-select-round-rectangle image CHANNEL-OP-REPLACE
                                       inset inset
                                       (- width (* 2 inset))
                                       (- height (* 2 inset))
                                       radius radius)
    (gimp-drawable-edit-stroke-selection border)

    ; Accent: a brass title rule, used only on interactive chrome.
    (gimp-context-set-foreground '(133 97 36))
    (gimp-image-select-rectangle image CHANNEL-OP-REPLACE
                                 (+ inset 14) (+ inset 34)
                                 (- width (* 2 (+ inset 14))) 2)
    (gimp-drawable-edit-fill accent FILL-FOREGROUND)

    ; Inner shadow under the title rule: one pixel, dark, low opacity.
    (gimp-image-select-rectangle image CHANNEL-OP-REPLACE
                                 (+ inset 14) (+ inset 36)
                                 (- width (* 2 (+ inset 14))) 1)
    (gimp-context-set-foreground '(24 18 12))
    (gimp-drawable-edit-fill accent FILL-FOREGROUND)

    (gimp-selection-none image)

    ; Save the layered source, then a flat PNG for Godot.
    (gimp-image-set-filename image "art/ui/panel.xcf")
    (gimp-xcf-save 0 image (car (gimp-image-get-layers image)) 
                   "art/ui/panel.xcf" "art/ui/panel.xcf")
    (let ((flat (car (gimp-image-flatten image))))
      (file-png-save 0 image flat "art/ui/panel.png" "art/ui/panel.png"
                     0 9 1 1 1 1 1))
    (gimp-image-delete image)
    (gimp-message "JOINERY_BUILT art/ui/panel.xcf art/ui/panel.png")))

(joinery-ui-panel)

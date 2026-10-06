# What each measurement is (and is not)

Colours are sRGB-ENCODED floats in 0-1 (not linear light). Luminance = 0.2126 R + 0.7152 G + 0.0722 B on those encoded values. Pixel coordinates are indices, x right, y down.
Anything bigger than `limits.analysis_max_side` (512) is area-averaged down for the art measurements (never upscaled); tiling, PBR and diff run at full resolution up to `limits.max_side_exact` (2048) and refuse larger images.

| Measurement | Definition | Cannot tell you |
|---|---|---|
| Silhouette mask | `alpha >= 0.5`, or RGB distance from the median border colour above `silhouette.bg_tolerance` (0-1 scale), or Otsu on luminance, or an explicit mask image | the subject on a busy background; whether two shapes are the same object |
| IoU / Dice | intersection over union of the two masks on the candidate's grid (the reference is resampled onto it) | anything about interior detail |
| Centroid / bbox offset | centroid = mean pixel index; offsets as fractions of image width/height | rotation, scale or perspective |
| Palette | deterministic k-means (k = `palette.k`) in sRGB from luminance-quantile starts, fixed-stride sampling | colour harmony, where colours are used |
| Palette distance | CIE76 delta-E in Lab (D65), chamfer both directions: image colours (share-weighted) to nearest target, target colours (weighted) to nearest image colour; the mean of the two | perceptual accuracy beyond CIE76 (no CIEDE2000) |
| Value structure | histogram, contrast = p95 - p5 of luminance, 3-level (dark < `value.dark_cut`, light > `value.light_cut`) and 5-level (0.2 steps) maps | whether the values read well |
| Edges | share of pixels with central-difference gradient magnitude above `edges.gradient_threshold`; distribution = normalised entropy of the edge pixels over a 4x4 grid | whether the edges are the right ones |
| Seam ratio | wrap-around step divided by the steps just inside both edges, per axis, worst axis (1.0 = continuous) | whether a seam is visible at the scale it is used |
| Repetition | highest circular autocorrelation outside the main lobe (1.0 = exact repeat), with its lag | repetition across many tiles |
| Albedo / roughness / metalness | luminance bounds fraction; grayscale spread, std and mean; share of in-between values | physical correctness for the material |
| Normal map | decoded vector = 2c - 1: mean length error, share of bad-length pixels, share with z < 0 | the green-channel convention (OpenGL vs DirectX) |
| Diff | MAE, RMSE, PSNR, changed-pixel share (largest channel difference above `diff.pixel_threshold`) and bounding box | perceptual difference, or whether a change was intended |

Limits and their ranges: `style/thresholds.yaml` (all placeholders; `pbr.*` bounds are flagged `verify_against_current_docs`).

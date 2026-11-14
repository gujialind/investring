# 品牌标识与 PWA 图标

InvestRing 品牌标识为「环形 + 突破箭头」，规格与取色决策见 `docs/design/visual-spec.md` §20.5。

## 资产清单

| 文件 | 用途 | 取色 |
| --- | --- | --- |
| `logo.svg` | 浅色背景固定色 mark | 环 `#2B5CD7` / 箭头 `#1A2333` |
| `logo-mono.svg` | 单色 mark（压印/单色场景） | 环与箭头皆 `#2B5CD7` |
| `logo-app.svg` | 应用图标矢量源（**全幅不透明方底** + 白 mark，mark 收进 maskable 安全区） | 渐变 `#3D6FE0→#1E3FA8` + 白 |
| `icon-192.png` | PWA 安装图标 192×192（`any maskable`） | 由 `logo-app.svg` 栅格化 |
| `icon-512.png` | PWA 安装 / splash 512×512（`any maskable`） | 由 `logo-app.svg` 栅格化 |

`manifest.json` 引用 `icon-192.png` / `icon-512.png`，purpose 为 `any maskable`。maskable 与 Apple 主屏幕都会自行裁圆，故源与位图**全幅不透明、无预圆角**（透明角会露底/黑角），且 mark 缩放收进中心 40% 半径的安全区防裁切。

## 运行时自适应标识（非本目录）

组件内双主题自适应标识走 `src/components/shared/BrandLogo.tsx`（环 = `hsl(var(--primary))`、箭头 = `hsl(var(--foreground))`，随主题 token；token 是裸 HSL 三元组须 `hsl()` 包裹）；Next 自动识别的 favicon 为 `src/app/icon.svg`（圆角方底 + 全尺寸 mark，浏览器标签页不遮罩、保 16px 辨识）、apple-touch-icon 为 `src/app/apple-icon.png`（由 `logo-app.svg` 栅格化的全幅不透明版）。本目录的固定色 SVG 用于品牌静态场景，不随主题切换。

## 更新约定

改动品牌图形时同步更新矢量源（`logo-app.svg`）与栅格 PNG（192/512 + `src/app/apple-icon.png`）；favicon `src/app/icon.svg` 共用同一 mark/渐变但保留圆角与全尺寸 mark（用途不同，不要求与全幅版逐像素一致）。PNG 须由矢量源栅格化，不手绘位图；改 `logo-app.svg` 后须复核位图四角与整幅 alpha 不透明、mark 在安全区内。
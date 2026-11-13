# 品牌标识与 PWA 图标

InvestRing 品牌标识为「环形 + 突破箭头」，规格与取色决策见 `docs/design/visual-spec.md` §20.5。

## 资产清单

| 文件 | 用途 | 取色 |
| --- | --- | --- |
| `logo.svg` | 浅色背景固定色 mark | 环 `#2B5CD7` / 箭头 `#1A2333` |
| `logo-mono.svg` | 单色 mark（压印/单色场景） | 环与箭头皆 `#2B5CD7` |
| `logo-app.svg` | 应用图标矢量源（圆角方底 + 白 mark） | 渐变 `#3D6FE0→#1E3FA8` + 白 |
| `icon-192.png` | PWA 安装图标 192×192 | 由 `logo-app.svg` 栅格化 |
| `icon-512.png` | PWA 安装 / splash 512×512 | 由 `logo-app.svg` 栅格化 |

`manifest.json` 引用 `icon-192.png` / `icon-512.png`。

## 运行时自适应标识（非本目录）

组件内双主题自适应标识走 `src/components/shared/BrandLogo.tsx`（环 = `var(--primary)`、箭头 = `var(--foreground)`，随主题 token）；Next 自动识别的 favicon 为 `src/app/icon.svg`、apple-touch-icon 为 `src/app/apple-icon.png`。本目录的固定色 SVG 用于品牌静态场景，不随主题切换。

## 更新约定

改动品牌图形时同步更新矢量源（`logo-app.svg`）与栅格 PNG（192/512），并保持 `src/app/icon.svg` / `apple-icon.png` 一致；PNG 须由矢量源栅格化，不手绘位图。
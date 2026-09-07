import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

// issue #127：禁止调色板硬编码颜色类名，一律走语义 token（docs/design/visual-spec.md §1.5）
// 全部源码适用（含豁免文件）。
const paletteColorSelectors = [
  {
    selector:
      "Literal[value=/(?:^|\\s)(?:text|bg|border)-(?:red|green|yellow|blue|amber|emerald|orange|purple|indigo|pink|teal|cyan)-\\d+/]",
    message:
      "禁止使用调色板颜色类名（如 text-red-500）。请改用语义 token（text-gain/text-loss/text-success/text-warning/text-destructive 及 -soft/-foreground 变体），见 docs/design/visual-spec.md。",
  },
  {
    selector:
      "TemplateElement[value.cooked=/(?:^|\\s)(?:text|bg|border)-(?:red|green|yellow|blue|amber|emerald|orange|purple|indigo|pink|teal|cyan)-\\d+/]",
    message:
      "禁止使用调色板颜色类名（如 text-red-500）。请改用语义 token（text-gain/text-loss/text-success/text-warning/text-destructive 及 -soft/-foreground 变体），见 docs/design/visual-spec.md。",
  },
];

// 2026-08-29：禁止任意值类名（visual-spec §1.5 第 2 道）。
// 范围：text-[…]（§5 四档之外）、p/px/py/pt/pb/pl/pr-[…]、gap/gap-x/gap-y-[…]、rounded-[…]（§7 派生档之外）。
// m 系 / space 系 / w·h 系任意值暂不拦截（多为视口比例或一次性尺寸，见 §1.5）。
const arbitraryValueSelectors = [
  {
    selector:
      "Literal[value=/(?:^|\\s)(?:text|p|px|py|pt|pb|pl|pr|gap|gap-x|gap-y|rounded)-\\[/]",
    message:
      "禁止使用任意值类名（text-[Npx]/p-[Npx]/gap-[Npx]/rounded-[Npx]）。字号走四级档位、间距圆角走派生档，见 docs/design/visual-spec.md §5/§7。",
  },
  {
    selector:
      "TemplateElement[value.cooked=/(?:^|\\s)(?:text|p|px|py|pt|pb|pl|pr|gap|gap-x|gap-y|rounded)-\\[/]",
    message:
      "禁止使用任意值类名（text-[Npx]/p-[Npx]/gap-[Npx]/rounded-[Npx]）。字号走四级档位、间距圆角走派生档，见 docs/design/visual-spec.md §5/§7。",
  },
];

// 2026-08-29（issue #249）：数值展示规范护栏（visual-spec §3/§8/§12）。
// ① JSX 内手写 "-" 占位 → 走 format 函数 fallback（统一 "--"）；
//    限定 JSXExpressionContainer 后代（三元分支的 Literal 是孙级），
//    不误伤 .replace("-", "/") 等 JSX 外的语义字符串。
// ② className 内联 font-mono tabular-nums → 用 number-cell utility
//   （需拼接时 getNumberCellClass()）；限定 JSXAttribute，不误伤 utils.ts 定义。
// ③ 份额语义量调 formatNumber → formatShares / formatSharesUnit（份额口径 2 位量化，
//    #87/#94）；覆盖 formatNumber(shares) 与 formatNumber(position.shares) 两种形态。
// 金额类 formatNumber 刻意不拦（概览大字「数字 + 元」为例外设计，见 §12），
// 切勿补全金额选择器——门禁与规范须保持一致。
const numberDisplaySelectors = [
  {
    selector: 'JSXExpressionContainer Literal[value="-"]',
    message:
      '禁止在 JSX 内手写 "-" 作为空值占位。请走 format 系函数 fallback（统一回显 "--"），见 docs/design/visual-spec.md §12（issue #249）。',
  },
  {
    selector:
      'JSXAttribute[name.name="className"] Literal[value=/font-mono tabular-nums/]',
    message:
      "禁止内联 font-mono tabular-nums。数值单元格请用 number-cell utility（需拼接类名时用 getNumberCellClass()），见 docs/design/visual-spec.md §3/§8（issue #249）。",
  },
  {
    selector:
      'JSXAttribute[name.name="className"] TemplateElement[value.cooked=/font-mono tabular-nums/]',
    message:
      "禁止内联 font-mono tabular-nums。数值单元格请用 number-cell utility（需拼接类名时用 getNumberCellClass()），见 docs/design/visual-spec.md §3/§8（issue #249）。",
  },
  {
    selector:
      'CallExpression[callee.name="formatNumber"] > Identifier[name=/[Ss]hares/]',
    message:
      "份额展示禁止用 formatNumber（可能偏离 2 位量化口径）。请用 formatShares / formatSharesUnit，见 docs/design/visual-spec.md §3（issue #249）。",
  },
  {
    selector:
      'CallExpression[callee.name="formatNumber"] > MemberExpression[property.name=/[Ss]hares/]',
    message:
      "份额展示禁止用 formatNumber（可能偏离 2 位量化口径）。请用 formatShares / formatSharesUnit，见 docs/design/visual-spec.md §3（issue #249）。",
  },
];

// 2026-09-07（issue #382）：e2e 定位器契约护栏（#217）。
// #217 当年以「grep 零残留」验收，但该结论从无机器保障，且实际不成立——#371/#372
// 实施中发现 4 处残留仍在按 lucide 图标类名与 Tailwind 工具类定位。此处结构性拦截。
// 不会误伤的既有合法写法：helpers.ts 的 `ancestor::div[@role="dialog"][1]`（不含
// contains(@class）、datepicker 的 `rdp-day_button`（react-day-picker 公开类名 API，
// 真正锚点是 data-day 属性）；注释不是 AST 节点，故 spec 里的历史说明文字也不命中。
const locatorContractSelectors = [
  {
    selector: "Literal[value=/\\.lucide-/]",
    message:
      "禁止按 lucide 图标类名定位（图标是实现细节，随 lucide-react 版本漂移）。请改用 data-testid 或 accessible name，见 #217/#382。",
  },
  {
    selector: "TemplateElement[value.cooked=/\\.lucide-/]",
    message:
      "禁止按 lucide 图标类名定位（图标是实现细节，随 lucide-react 版本漂移）。请改用 data-testid 或 accessible name，见 #217/#382。",
  },
  {
    selector: "Literal[value=/contains\\(@class/]",
    message:
      "禁止在 xpath 中按 class 匹配祖先/后代（耦合 Tailwind 工具类与 shadcn 基件样式）。请在组件侧补 data-testid 后按 testid 定位，见 #217/#382。",
  },
  {
    selector: "TemplateElement[value.cooked=/contains\\(@class/]",
    message:
      "禁止在 xpath 中按 class 匹配祖先/后代（耦合 Tailwind 工具类与 shadcn 基件样式）。请在组件侧补 data-testid 后按 testid 定位，见 #217/#382。",
  },
  {
    selector: "Literal[value=/cursor-pointer/]",
    message:
      "禁止按 Tailwind 工具类定位。请改用 data-testid，见 #217/#372/#382。",
  },
  {
    selector: "TemplateElement[value.cooked=/cursor-pointer/]",
    message:
      "禁止按 Tailwind 工具类定位。请改用 data-testid，见 #217/#372/#382。",
  },
];

const eslintConfig = [
  // 复刻 `next lint` 的默认忽略范围，避免扫描 node_modules / 构建产物
  {
    ignores: [
      "**/node_modules/**",
      ".next/**",
      "out/**",
      "build/**",
      "dist/**",
      "coverage/**",
      "next-env.d.ts",
    ],
  },
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    // 只作用于前端源码；排除配置文件自身（message 示例文本也会被 selector 匹配）
    files: ["src/**"],
    rules: {
      // Next 15 + React 19 新 JSX 转换不再需要显式 React 导入
      "react/react-in-jsx-scope": "off",
      // eslint-config-next v16 新增规则，存量代码大量命中，暂不启用
      "react-hooks/set-state-in-effect": "off",
      "@next/next/no-location-assign-relative-destination": "off",
      "no-restricted-syntax": [
        "error",
        ...paletteColorSelectors,
        ...arbitraryValueSelectors,
        ...numberDisplaySelectors,
      ],
      // 2026-09-01（issue #349）：份额上屏一律 formatSharesUnit（visual-spec §3/§12）。
      // 业务代码禁止 import 裸 formatShares；豁免文件见下方豁免块。
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@/lib/utils",
              importNames: ["formatShares"],
              message:
                "份额展示一律用 formatSharesUnit（visual-spec §3/§12，issue #349）；formatShares 仅供 formatSharesUnit 内部实现。",
            },
          ],
        },
      ],
    },
  },
  // formatShares import 门禁豁免（visual-spec §1.5 登记）：单测需直测基础函数。
  {
    files: ["src/lib/utils.test.ts"],
    rules: {
      "no-restricted-imports": "off",
    },
  },
  // Node 配置文件为 CJS 语境，require 属正当用法（#375：next.config.js 读 package.json version 注入 NEXT_PUBLIC_APP_VERSION）。
  {
    files: ["next.config.js"],
    rules: {
      "@typescript-eslint/no-require-imports": "off",
    },
  },
  // 任意值护栏豁免（visual-spec §1.5 登记；豁免文件仍受调色板拦截）。
  // 永久：shadcn 基件 vendor 源码，保持与上游同步。
  {
    files: ["src/components/ui/**"],
    rules: {
      "no-restricted-syntax": ["error", ...paletteColorSelectors],
    },
  },
  // 临时（ratchet，只减不增）：存量 text-[Npx] 13 处，改动页面顺手收敛后从此清单移出。
  {
    files: [
      "src/app/portfolio/\\[code\\]/page.tsx",
      "src/components/shared/PositionSections.tsx",
      "src/components/layout/NotificationBell.tsx",
    ],
    rules: {
      "no-restricted-syntax": ["error", ...paletteColorSelectors],
    },
  },
  // e2e 定位器契约（#217/#382）：此前所有 no-restricted-syntax 块均为 src/** 作用域，
  // 结构上覆盖不到 e2e/，故残留能长期存在。用 e2e/**/*.ts 以显式匹配任意深度
  // （含直接子文件 helpers.ts 与 fixtures/auth.setup.ts）。
  {
    files: ["e2e/**/*.ts"],
    rules: {
      "no-restricted-syntax": ["error", ...locatorContractSelectors],
    },
  },
];

export default eslintConfig;

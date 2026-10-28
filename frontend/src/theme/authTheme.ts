import { theme, type ThemeConfig } from 'antd'

/**
 * 登录 / 注册页主题：暖白画布 + 暖金强调色 + 填充式圆角控件。
 *
 * 参考 getdesign.md / Apple（"Premium white space, SF Pro"）。
 *
 * 只调 token 不覆写状态色：defaultAlgorithm 会据此推导出
 * 悬停、聚焦、禁用、错误的整套色阶，CSS 里几乎不用写颜色。
 *
 * 这个主题只作用于登录/注册子树；后台业务页仍用全局主题。
 */
export const authTheme: ThemeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: '#f2b544',
    colorBgBase: '#fbf9f4',
    colorBgContainer: '#f5f3ee',
    colorText: '#1d1d1f',
    colorTextPlaceholder: '#a1a1a6',
    colorBorder: '#e8e4da',
    colorError: '#c0392b',
    // Apple 的圆角偏大：12px 用于控件，20px 用于卡片（卡片在 CSS 里）
    borderRadius: 12,
    controlHeight: 48,
    fontSize: 16,
    fontFamily: 'inherit',
  },
  components: {
    Input: {
      paddingInline: 14,
      activeShadow: 'none',
      hoverBorderColor: 'transparent',
      activeBorderColor: 'transparent',
      colorBgContainer: '#f5f3ee',
    },
    Button: {
      controlHeight: 52,
      fontWeight: 600,
      primaryShadow: 'none',
    },
    Form: {
      labelColor: '#6e6e73',
      labelFontSize: 12.5,
      itemMarginBottom: 20,
      verticalLabelPadding: '0 0 8px',
    },
    Alert: {
      colorErrorBg: '#fdf1ef',
      colorErrorBorder: '#efc9c3',
    },
  },
}

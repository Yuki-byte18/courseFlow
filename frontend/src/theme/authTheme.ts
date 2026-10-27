import { theme, type ThemeConfig } from 'antd'

/**
 * 登录 / 注册页专用主题：赛博朋克（近黑底 + 青/品红双霓虹）。
 *
 * 用 antd 的 darkAlgorithm 处理所有状态对比度，我们只调少量 token，
 * CSS 才能保持短。圆角统一 0 —— 直角属于终端与图纸，不属于卡片。
 */
export const authTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: '#22d3ee',
    colorBgBase: '#070b14',
    colorBgContainer: 'transparent',
    colorText: '#e8eef7',
    colorTextPlaceholder: 'rgba(232, 238, 247, 0.26)',
    colorBorder: 'rgba(34, 211, 238, 0.24)',
    colorError: '#ff4d6d',
    borderRadius: 0,
    controlHeight: 44,
    fontSize: 14,
    fontFamily: 'inherit',
  },
  components: {
    // 输入框去掉自带边框，改由 CSS 在下划线容器上画线
    Input: {
      paddingInline: 0,
      activeShadow: 'none',
      hoverBorderColor: 'transparent',
      activeBorderColor: 'transparent',
    },
    Button: {
      controlHeight: 48,
      fontWeight: 600,
      primaryShadow: 'none',
    },
    Form: {
      labelColor: 'rgba(219, 230, 242, 0.38)',
      labelFontSize: 11,
      itemMarginBottom: 22,
      verticalLabelPadding: '0 0 8px',
    },
    Alert: {
      colorErrorBg: 'rgba(255, 77, 109, 0.08)',
      colorErrorBorder: 'rgba(255, 77, 109, 0.32)',
    },
  },
}

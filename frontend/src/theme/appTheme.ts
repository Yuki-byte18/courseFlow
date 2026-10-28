import { theme, type ThemeConfig } from 'antd'

/**
 * 全站 antd 主题：暖白 + 暖金，与登录页同一套语言。
 *
 * 数值必须和 src/styles/tokens.css 里的 CSS 变量一致 ——
 * 那些变量给手写样式用，这份给 antd 组件用，两边对不上就会出现
 * "按钮是 #f2b544、标签是 #f7c25c" 这种细微割裂。
 */
export const appTheme: ThemeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: '#f2b544',
    colorBgBase: '#fbf9f4',
    colorBgContainer: '#ffffff',
    colorBgLayout: '#fbf9f4',
    colorText: '#1d1d1f',
    colorTextSecondary: '#6e6e73',
    colorTextTertiary: '#a1a1a6',
    colorTextPlaceholder: '#a1a1a6',
    colorBorder: '#e8e4da',
    colorBorderSecondary: '#efece4',
    colorError: '#c0392b',
    colorSuccess: '#34c759',
    borderRadius: 12,
    controlHeight: 40,
    fontSize: 14,
    fontFamily: 'inherit',
  },
  components: {
    Button: {
      primaryShadow: 'none',
      fontWeight: 600,
    },
    Input: {
      activeShadow: '0 0 0 4px rgba(242, 181, 68, 0.22)',
    },
    Table: {
      headerBg: '#fcfbf8',
      headerColor: '#6e6e73',
      headerSplitColor: 'transparent',
      borderColor: '#efece4',
      rowHoverBg: '#fcfaf5',
      cellPaddingBlock: 14,
    },
    Modal: {
      borderRadiusLG: 18,
    },
    Select: {
      optionSelectedBg: 'rgba(242, 181, 68, 0.14)',
    },
    Tag: {
      defaultBg: '#f5f3ee',
      defaultColor: '#6e6e73',
    },
    Drawer: {
      colorBgElevated: '#ffffff',
    },
  },
}

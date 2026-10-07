import { BarChart, CandlestickChart, LineChart, PieChart, RadarChart, ScatterChart } from 'echarts/charts';
import {
  AxisPointerComponent,
  DatasetComponent,
  DataZoomComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TitleComponent,
  ToolboxComponent,
  TooltipComponent,
  TransformComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import {
  LabelLayout,
  LegacyGridContainLabel,
  UniversalTransition,
} from 'echarts/features';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([
  TitleComponent,
  PieChart,
  RadarChart,
  TooltipComponent,
  GridComponent,
  DatasetComponent,
  TransformComponent,
  BarChart,
  LineChart,
  LabelLayout,
  LegacyGridContainLabel,
  UniversalTransition,
  CanvasRenderer,
  LegendComponent,
  ToolboxComponent,
  GraphicComponent,
  // Minerva: the ETF flow page (index candles, zoom, abnormal-day pins).
  CandlestickChart,
  DataZoomComponent,
  MarkPointComponent,
  AxisPointerComponent,
  // Minerva: the money map (industry bubbles, zone areas, threshold lines).
  ScatterChart,
  MarkAreaComponent,
  MarkLineComponent,
]);
export type { ECOption } from './types';

export default echarts;

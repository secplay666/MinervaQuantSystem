"""SW (申万) 2021 level-1 industry indices, the money map's data.

The service (swsresearch.com, through AKShare ``index_hist_sw``) returns an
index's whole daily history on each call, volume in 100 million shares and
amount in 100 million CNY.  Sixteen indices start in 1999, twelve on
2014-02-21, and the three created in 2021 (石油石化, 美容护理, 环保) on
2021-12-13.
"""

from __future__ import annotations

SW_L1: dict[str, str] = {
    "801010": "农林牧渔", "801030": "基础化工", "801040": "钢铁", "801050": "有色金属", "801080": "电子",
    "801110": "家用电器", "801120": "食品饮料", "801130": "纺织服饰", "801140": "轻工制造", "801150": "医药生物",
    "801160": "公用事业", "801170": "交通运输", "801180": "房地产", "801200": "商贸零售", "801210": "社会服务",
    "801230": "综合", "801710": "建筑材料", "801720": "建筑装饰", "801730": "电力设备", "801740": "国防军工",
    "801750": "计算机", "801760": "传媒", "801770": "通信", "801780": "银行", "801790": "非银金融",
    "801880": "汽车", "801890": "机械设备", "801950": "煤炭", "801960": "石油石化", "801970": "环保",
    "801980": "美容护理",
}

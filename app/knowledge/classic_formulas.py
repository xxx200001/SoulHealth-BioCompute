"""经典底方库：辨证 → 立法 → 底方 → 加减 的知识数据层。

设计对齐真实中医开方流程：先定主证、再立治则、选经典方为底方、按兼证加减，
而不是"看到哪个指标异常就往里塞一味药"。

【目录约束下的化裁——如实说明】
本系统只能使用药食同源目录内的原料，而大量经典方含处方药材（逍遥散之柴胡白芍、
四妙散之苍术黄柏牛膝、二陈汤之半夏均不在目录）。因此每个底方均为"化裁"：
- source 标注原方出处；
- removed 逐味记录因目录限制去掉了什么（骨架诚实，不假装开出了原方）；
- 无合适经典方可化裁的方向（如湿热），以"自拟，取 XX 方之意"明示。
这正是现实食养茶饮行业的合规做法。所有底方原料均已核对在目录内。

【结构】
CLASSIC_FORMULAS: 底方库。base 为君臣佐使骨架（保持不变的部分）。
SYNDROME_TO_FORMULA: 证型 → 底方映射。
PRIMARY_PRIORITY: 多证共存时的主证优先级（外感先解表、急则治标、慢性调理靠后）。
ADDITION_RULES: 兼证/兼风险加减规则，候选制（走目录门禁），每条附依据。
METABOLIC_TRIGGERS: 无自述证型时，哪些生物医学标签可启用代谢兜底底方。
"""
from __future__ import annotations

from typing import Dict, List, Set

# ------------------------------------------------------------------ 底方库

CLASSIC_FORMULAS: Dict[str, dict] = {
    "sang_ju_yin": {
        "name": "桑菊饮化裁",
        "source": "《温病条辨》桑菊饮",
        "principle": "疏风清热，宣肺利咽",
        "removed": ["连翘（不在药食同源目录，去之）"],
        "forbid_tonic": True,  # 外感忌补：兼证加减跳过补益壅滞之品
        "base": [
            {"name": "桑叶", "dose": 7, "role": "君", "note": "疏散风热、清肺"},
            {"name": "菊花", "dose": 4, "role": "君", "note": "疏风清热、清利头目"},
            {"name": "薄荷", "dose": 3, "role": "臣", "note": "辛凉透表（后下）"},
            {"name": "桔梗", "dose": 5, "role": "臣", "note": "宣肺利咽"},
            {"name": "杏仁（甜）", "dose": 6, "role": "臣", "note": "宣降肺气（仅用甜杏仁）"},
            {"name": "鲜芦根", "dose": 15, "role": "佐", "note": "清热生津护津"},
            {"name": "甘草", "dose": 3, "role": "使", "note": "调和诸药、利咽"},
        ],
    },
    "shen_ling_baizhu": {
        "name": "参苓白术散化裁",
        "source": "《太平惠民和剂局方》参苓白术散",
        "principle": "益气健脾，渗湿止泻",
        "removed": ["白术（不在目录，去之）", "砂仁（香料类不入茶方，去之）"],
        "base": [
            {"name": "党参", "dose": 8, "role": "君", "note": "益气健脾（试点目录品种）"},
            {"name": "茯苓", "dose": 8, "role": "君", "note": "健脾渗湿"},
            {"name": "山药", "dose": 12, "role": "臣", "note": "补脾益气养阴"},
            {"name": "白扁豆", "dose": 10, "role": "臣", "note": "健脾化湿（炒制品）"},
            {"name": "莲子", "dose": 10, "role": "臣", "note": "补脾止泻"},
            {"name": "薏苡仁", "dose": 10, "role": "佐", "note": "淡渗利湿"},
            {"name": "桔梗", "dose": 3, "role": "佐", "note": "载药上行、宣肺"},
            {"name": "甘草", "dose": 3, "role": "使", "note": "调和诸药"},
        ],
    },
    "qing_li_yin": {
        "name": "清利饮（自拟）",
        "source": "自拟方，取《成方便读》四妙散清热利湿之意",
        "principle": "清热利湿，导浊下行",
        "removed": ["四妙散原方苍术、黄柏、牛膝均不在目录，无法化裁复现，"
                    "故以目录内清利之品自拟，如实标注"],
        "base": [
            {"name": "蒲公英", "dose": 12, "role": "君", "note": "清热解毒利湿"},
            {"name": "栀子", "dose": 5, "role": "臣", "note": "清利三焦（炒制品）"},
            {"name": "薏苡仁", "dose": 10, "role": "佐", "note": "淡渗利湿、护脾制衡苦寒"},
            {"name": "赤小豆", "dose": 8, "role": "佐", "note": "利水渗湿"},
            {"name": "淡竹叶", "dose": 6, "role": "佐", "note": "清心利尿"},
            {"name": "甘草", "dose": 3, "role": "使", "note": "调和、缓苦寒之性"},
        ],
    },
    "yi_wei_tang": {
        "name": "益胃汤化裁",
        "source": "《温病条辨》益胃汤",
        "principle": "甘寒养阴，益胃生津",
        "removed": ["北沙参（不在目录，去之）", "冰糖（改为可选，血糖异常者不加）"],
        "base": [
            {"name": "麦冬", "dose": 10, "role": "君", "note": "养阴生津润肺（2023 年增补品种）"},
            {"name": "玉竹", "dose": 10, "role": "臣", "note": "养阴润燥"},
            {"name": "地黄", "dose": 10, "role": "臣", "note": "清热凉血养阴（生地黄）"},
            {"name": "枸杞子", "dose": 6, "role": "佐", "note": "平补肝肾"},
            {"name": "甘草", "dose": 2, "role": "使", "note": "调和诸药"},
        ],
    },
    "suan_zao_ren_tang": {
        "name": "酸枣仁汤合百合地黄汤意化裁",
        "source": "《金匮要略》酸枣仁汤、百合地黄汤",
        "principle": "养血安神，清心除烦",
        "removed": ["知母、川芎（不在目录，去之）；合百合地黄汤之意以百合宁心"],
        "base": [
            {"name": "酸枣仁", "dose": 12, "role": "君", "note": "养心安神（炒制品）"},
            {"name": "百合", "dose": 10, "role": "臣", "note": "清心安神、养阴"},
            {"name": "茯苓", "dose": 8, "role": "臣", "note": "宁心健脾"},
            {"name": "桂圆肉", "dose": 6, "role": "佐", "note": "补益心脾养血（血糖异常自动规避）"},
            {"name": "甘草", "dose": 3, "role": "使", "note": "调和诸药"},
        ],
    },
    "sheng_mai_yin": {
        "name": "生脉饮合玉屏风散意化裁",
        "source": "《医学启源》生脉饮、《究原方》玉屏风散",
        "principle": "益气生津，固表扶正",
        "removed": ["五味子（不在目录，去之）", "白术、防风（不在目录），以黄芪、山药益气固本"],
        "base": [
            {"name": "西洋参", "dose": 5, "role": "君", "note": "补气养阴、清虚热"},
            {"name": "黄芪", "dose": 8, "role": "君", "note": "补气升阳固表（试点目录品种）"},
            {"name": "麦冬", "dose": 8, "role": "臣", "note": "养阴生津"},
            {"name": "山药", "dose": 12, "role": "佐", "note": "平补脾肺肾"},
            {"name": "大枣", "dose": 9, "role": "使", "note": "补中和药（血糖/血脂异常自动规避）"},
        ],
    },
    "run_chang_yin": {
        "name": "润肠饮（自拟）",
        "source": "自拟方，取《世医得效方》五仁丸润下之意",
        "principle": "润肠通便，润下不峻下",
        "removed": ["五仁丸之桃仁（孕妇禁忌且有小毒，本系统不入茶方）、柏子仁、松子仁"
                    "（不在目录）；以蜂蜜润之"],
        "base": [
            {"name": "火麻仁", "dose": 12, "role": "君", "note": "润肠通便（碾碎）"},
            {"name": "郁李仁", "dose": 6, "role": "臣", "note": "润下行气（捣碎）"},
            {"name": "杏仁（甜）", "dose": 6, "role": "臣", "note": "降气润肠"},
            {"name": "陈皮", "dose": 3, "role": "佐", "note": "理气行滞、助通降"},
            {"name": "蜂蜜", "dose": 20, "role": "使", "note": "润燥和中（温水冲调，"
                                                            "血糖异常自动规避）"},
        ],
    },
    "xing_su_yin": {
        "name": "杏苏散化裁",
        "source": "《温病条辨》杏苏散",
        "principle": "轻宣凉燥，理肺化痰",
        "removed": ["半夏（有毒不在目录）、前胡、枳壳（不在目录，去之）；加化橘红增化痰之力"],
        "base": [
            {"name": "杏仁（甜）", "dose": 8, "role": "君", "note": "宣肺润燥止咳（仅甜杏仁）"},
            {"name": "紫苏", "dose": 4, "role": "君", "note": "轻宣发表（后下）"},
            {"name": "化橘红", "dose": 4, "role": "臣", "note": "理气化痰（2023 年增补品种）"},
            {"name": "桔梗", "dose": 4, "role": "臣", "note": "宣肺利咽"},
            {"name": "陈皮", "dose": 4, "role": "佐", "note": "理气燥湿"},
            {"name": "茯苓", "dose": 8, "role": "佐", "note": "健脾渗湿以杜生痰之源"},
            {"name": "甘草", "dose": 3, "role": "使", "note": "调和诸药"},
        ],
    },
    "huo_xiang_yin": {
        "name": "藿香正气散化裁",
        "source": "《太平惠民和剂局方》藿香正气散",
        "principle": "芳香化浊，解暑和中",
        "removed": ["半夏（有毒）、厚朴、白术、大腹皮（不在目录，去之）"],
        "base": [
            {"name": "藿香", "dose": 6, "role": "君", "note": "芳香化浊解暑（后下）"},
            {"name": "紫苏", "dose": 3, "role": "臣", "note": "发表和中（后下）"},
            {"name": "白扁豆花", "dose": 6, "role": "臣", "note": "解暑化湿"},
            {"name": "陈皮", "dose": 4, "role": "佐", "note": "理气和中"},
            {"name": "茯苓", "dose": 8, "role": "佐", "note": "健脾渗湿"},
            {"name": "生姜", "dose": 2, "role": "使", "note": "和胃止呕、调和"},
        ],
    },
    "jie_geng_tang": {
        "name": "桔梗汤加味",
        "source": "《伤寒论》桔梗汤",
        "principle": "宣肺利咽，清热生津",
        "removed": ["原方仅桔梗、甘草二味且均在目录；加青果、罗汉果、薄荷增利咽生津之力"],
        "base": [
            {"name": "桔梗", "dose": 5, "role": "君", "note": "宣肺利咽（原方君药）"},
            {"name": "甘草", "dose": 3, "role": "臣", "note": "清热解毒利咽（原方臣药）"},
            {"name": "青果", "dose": 6, "role": "佐", "note": "利咽生津"},
            {"name": "罗汉果", "dose": 5, "role": "佐", "note": "清肺利咽"},
            {"name": "薄荷", "dose": 2, "role": "使", "note": "辛凉利咽（后下）"},
        ],
    },
    "bao_he_fang": {
        "name": "保和丸化裁·消导化浊方",
        "source": "《丹溪心法》保和丸",
        "principle": "消食导滞，化浊和中",
        "removed": ["神曲（不在目录）、半夏（有毒）、连翘（不在目录），去之；"
                    "以麦芽合莱菔子增消导之力"],
        "sex_envoy": {"female": ("玫瑰花", 3, "疏肝和血、调和诸药"),
                      "default": ("甘草", 3, "调和诸药")},
        "base": [
            {"name": "山楂", "dose": 8, "role": "君", "note": "消食化积、行气散瘀（炒制品）"},
            {"name": "茯苓", "dose": 8, "role": "臣", "note": "健脾渗湿"},
            {"name": "陈皮", "dose": 4, "role": "臣", "note": "理气和中"},
            {"name": "莱菔子", "dose": 5, "role": "佐", "note": "消食下气（炒制品）"},
            {"name": "麦芽", "dose": 6, "role": "佐", "note": "消食和中（炒制品）"},
        ],
    },
}

# ------------------------------------------------------------------ 证型 → 底方

SYNDROME_TO_FORMULA: Dict[str, str] = {
    "wind_heat_pattern": "sang_ju_yin",
    "summer_damp_pattern": "huo_xiang_yin",
    "damp_heat_pattern": "qing_li_yin",
    "cough_phlegm_pattern": "xing_su_yin",
    "constipation_pattern": "run_chang_yin",
    "spleen_damp_pattern": "shen_ling_baizhu",
    "throat_pattern": "jie_geng_tang",
    "insomnia_pattern": "suan_zao_ren_tang",
    "yin_deficiency_pattern": "yi_wei_tang",
    "qi_deficiency_pattern": "sheng_mai_yin",
}

# 主证优先级：外感先解表 → 暑湿/湿热等急证 → 肺系/腑气 → 脾胃 → 慢性调理。
PRIMARY_PRIORITY: List[str] = [
    "wind_heat_pattern", "summer_damp_pattern", "damp_heat_pattern",
    "cough_phlegm_pattern", "constipation_pattern", "spleen_damp_pattern",
    "throat_pattern", "insomnia_pattern", "yin_deficiency_pattern",
    "qi_deficiency_pattern",
]

# 无自述证型时，命中这些生物医学标签即启用代谢兜底底方（保和丸化裁）。
METABOLIC_TRIGGERS: Set[str] = {
    "obesity", "overweight", "fatty_liver_us", "nash_possible",
    "liver_enzyme_elevated", "pancreatic_steatosis_possible",
    "insulin_resistance_risk", "glucose_high", "dyslipidemia",
    "hyperuricemia", "blood_pressure_high",
}

# 无自述证型时，不同代谢风险方向兜底底方优先级
RISK_FALLBACK_PRIORITY: List[tuple] = [
    ({"fatty_liver_us", "nash_possible", "liver_enzyme_elevated", "pancreatic_steatosis_possible"},
     "bao_he_fang", "存在肝胆/消化代谢风险，选用保和丸化裁消导化浊"),
    ({"glucose_high", "insulin_resistance_risk"},
     "yi_wei_tang", "存在糖代谢/胰岛素抵抗风险，选用益胃汤化裁养阴生津"),
    ({"hyperuricemia", "dyslipidemia", "blood_pressure_high"},
     "qing_li_yin", "存在浊资/湿热代谢风险，选用清利饮清热利湿"),
]

# 补益壅滞之品（外感主证时禁止加入——"外感忌补"治则）
TONIC_NAMES: Set[str] = {"黄芪", "党参", "人参", "西洋参", "桂圆肉", "大枣",
                         "蜂蜜", "灵芝", "山药", "肉苁蓉", "阿胶"}

# ------------------------------------------------------------------ 加减规则
# when: 命中任一 id 即触发（风险标签或证型 id，主证自身不重复触发）
# add:  候选制 [(name, dose), ...] —— 逐个过目录门禁与 AVOID_IF，取首个可用者；
#       need 指定本条最多加几味（默认 1）。
# reason: 加减依据，写入报告的"加减与化裁依据"留痕。

ADDITION_RULES: List[dict] = [
    {"when": {"glucose_high", "insulin_resistance_risk"}, "need": 2,
     "add": [("桑叶", 5), ("葛根", 6)],
     "reason": "兼血糖偏高/胰岛素抵抗风险：加桑叶、葛根，生津清降"
               "（桑叶止渴、葛根生津为传统食养方向）"},
    {"when": {"hyperuricemia"}, "need": 2,
     "add": [("玉米须", 6), ("菊苣", 10), ("赤小豆", 6), ("鲜白茅根", 15)],
     "reason": "兼尿酸偏高：加利湿泄浊之品（候选依次核对目录门禁）"},
    {"when": {"dyslipidemia"}, "need": 2,
     "add": [("山楂", 8), ("决明子", 5)],
     "reason": "兼血脂异常：加山楂消积、决明子润降"},
    {"when": {"blood_pressure_high"}, "need": 2,
     "add": [("菊花", 4), ("决明子", 5)],
     "reason": "兼血压偏高：加菊花、决明子平肝清降（甘草同时自动规避）"},
    {"when": {"fatty_liver_us", "nash_possible", "obesity"}, "need": 1,
     "add": [("荷叶", 4)],
     "reason": "兼形盛脂浊：加荷叶化浊升清"},
    {"when": {"liver_enzyme_elevated"}, "need": 2,
     "add": [("枸杞子", 6), ("菊花", 4)],
     "reason": "兼肝酶升高：加杞菊养肝方向食养参考（病因仍须医生鉴别）"},
    {"when": {"pancreatic_steatosis_possible"}, "need": 1,
     "add": [("薏苡仁", 10)],
     "reason": "兼胰腺回声改变：加薏苡仁淡渗利湿"},
    # ---- 兼证证型（非主证时作加味处理）----
    {"when": {"insomnia_pattern"}, "need": 1,
     "add": [("酸枣仁", 10)],
     "reason": "兼睡眠不佳：加酸枣仁养心安神"},
    {"when": {"throat_pattern"}, "need": 2,
     "add": [("桔梗", 4), ("青果", 6)],
     "reason": "兼咽喉不适：加桔梗、青果利咽"},
    {"when": {"yin_deficiency_pattern"}, "need": 1,
     "add": [("麦冬", 8)],
     "reason": "兼阴虚津亏：加麦冬养阴生津"},
    {"when": {"qi_deficiency_pattern"}, "need": 1,
     "add": [("黄芪", 8)],
     "reason": "兼气虚疲乏：加黄芪益气（外感主证时按「外感忌补」自动跳过）"},
    {"when": {"damp_heat_pattern"}, "need": 1,
     "add": [("蒲公英", 10)],
     "reason": "兼湿热之象：加蒲公英清利（中病即止）"},
    {"when": {"spleen_damp_pattern"}, "need": 1,
     "add": [("白扁豆", 10)],
     "reason": "兼脾虚湿盛：加炒白扁豆健脾化湿"},
    {"when": {"cough_phlegm_pattern"}, "need": 1,
     "add": [("化橘红", 4)],
     "reason": "兼咳嗽有痰：加化橘红理气化痰"},
    {"when": {"summer_damp_pattern"}, "need": 1,
     "add": [("藿香", 5)],
     "reason": "兼暑湿困表：加藿香芳香化浊（后下）"},
    {"when": {"constipation_pattern"}, "need": 1,
     "add": [("火麻仁", 10)],
     "reason": "兼大便干结：加火麻仁润肠（便溏证同现时该证型已被互斥剔除）"},
]

MAX_TOTAL = 10        # 成方总味数上限（真实处方通常 5–8 味，含加减封顶 10）
MAX_ADDITIONS = 4     # 加减规则最多采纳条数（防止兼证过多导致药味堆叠）

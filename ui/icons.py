# -*- coding: utf-8 -*-
"""
Библиотека профессиональных векторных SVG-иконок в стиле Lucide / Feather / Lightshot.
Обеспечивает монохромный, аккуратный и минималистичный вид без смайликов и эмодзи.
Поддерживает HiDPI масштабирование, темную и светлую тему оформления.
"""

from PyQt6.QtCore import QByteArray, Qt, QSize, QPointF, QRectF
from PyQt6.QtGui import QPixmap, QPainter, QIcon, QColor, QPen, QBrush, QPolygonF, QCursor
from PyQt6.QtSvg import QSvgRenderer

SVG_ICONS = {
    # --- Инструменты рисования (Правая панель) ---
    "chevron_up": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="18 15 12 9 6 15"/>
    </svg>""",

    "chevron_down": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9"/>
    </svg>""",

    "chevron_left": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="15 18 9 12 15 6"/>
    </svg>""",

    "chevron_right": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="9 18 15 12 9 6"/>
    </svg>""",

    "more_vertical": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
        <circle cx="12" cy="5" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="12" cy="19" r="1.7"/>
    </svg>""",

    "move": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="5 9 2 12 5 15"/>
        <polyline points="9 5 12 2 15 5"/>
        <polyline points="15 19 12 22 9 19"/>
        <polyline points="19 9 22 12 19 15"/>
        <line x1="2" y1="12" x2="22" y2="12"/>
        <line x1="12" y1="2" x2="12" y2="22"/>
    </svg>""",

    "select": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <rect x="4" y="4" width="16" height="16" rx="1" stroke-dasharray="3 2"/>
        <path d="M8 2v4M16 2v4M8 18v4M16 18v-4"/>
    </svg>""",

    "more": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
        <circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/>
    </svg>""",

    "google_lens": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 9V6a2 2 0 0 1 2-2h3M15 4h3a2 2 0 0 1 2 2v3M20 15v3a2 2 0 0 1-2 2h-3M9 20H6a2 2 0 0 1-2-2v-3"/>
        <circle cx="12" cy="12" r="3.5"/>
    </svg>""",

    "yandex_images": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1095 1095" fill="currentColor">
        <path d="M838.72 93.5C860.31 93.39 881.91 93.58 903.5 93.66C917.39 93.71 931.99 92.25 945.72 94.59C972.01 99.09 992.97 116.04 1005.18 139.32C1009.22 147 1011.71 155.91 1012.89 164.5C1014.21 174.06 1013.35 183.88 1013.43 193.5C1013.61 214.83 1013.14 236.18 1013.4 257.5C1013.67 279.16 1013.28 300.84 1013.62 322.5C1013.98 346.22 1016.55 368.83 991.87 381.38C967.58 393.74 934.97 377.87 933.86 349.5C933.43 338.54 934.03 327.47 934.08 316.5C934.24 283.83 935.04 251.17 935.08 218.5C935.08 211.5 935.16 204.5 935.13 197.5C935.07 186.99 935.98 176.64 927.62 168.89C919.18 161.07 909.31 161.73 898.5 161.72C891.17 161.72 883.83 161.7 876.5 161.69C832.84 161.63 789.17 161.73 745.5 161.81C737.17 161.82 728.83 161.8 720.5 161.78C715.14 161.77 709.52 162.57 704.24 161.41C693.91 159.13 685.23 150.96 681.2 141.3C673.1 121.89 684.9 98.53 705.67 94.34C710.87 93.29 716.23 93.7 721.5 93.68C730.83 93.64 740.17 93.74 749.5 93.7C779.24 93.56 808.98 93.64 838.72 93.5ZM153.57 94.35C162.46 93.25 171.56 93.84 180.5 93.82C196.5 93.77 212.5 93.78 228.5 93.83C271.17 93.95 313.84 93.71 356.5 93.64C378.15 93.61 402.34 89.73 414.28 112.26C415.94 115.4 417.23 119.03 417.7 122.56C418.25 126.79 418.16 131.38 417.21 135.54C416.4 139.11 415.19 142.64 413.34 145.81C401.55 166.06 377.55 162.84 357.5 162.91C311.84 163.06 266.17 162.56 220.5 162.76C210.5 162.8 200.5 162.71 190.5 162.73C182.4 162.74 174.88 162.38 168.24 167.73C154.97 178.43 158.97 198.49 159.16 213.5C159.67 253.16 160.07 292.84 160.19 332.5C160.21 340.86 161.44 350.38 159 358.48C153.79 375.8 136.67 387.47 118.56 385.64C114.27 385.2 110 384.13 106 382.49C92.28 376.88 83.91 363.36 82.51 348.98C81.95 343.2 82.54 337.3 82.55 331.5C82.58 319.83 82.76 308.17 82.73 296.5C82.69 279.83 83.17 263.17 83.25 246.5C83.35 228.5 83.38 210.5 83.35 192.5C83.32 176.67 82.93 160.94 88.4 145.83C95.24 126.93 109.44 112.9 126.33 102.84C134.36 98.06 144.37 95.5 153.57 94.35ZM717.48 279.5C717.48 460.2 717.48 640.9 717.48 821.61C712.16 823.97 697.09 822.56 690.5 822.56C675.86 822.57 661.21 822.29 646.57 822.52C638.26 822.65 628.41 823.93 620.42 821.73C620.42 667.65 620.42 513.58 620.42 359.5C612.22 357.71 602.91 358.71 594.5 358.75C568.86 358.87 544.32 362.21 522.93 377.45C482.39 406.34 474.12 469.06 499.3 510.23C514.87 535.69 549.77 557.33 574.14 574.38C580.21 578.63 586.09 583.1 592.12 587.41C595.7 589.97 600.11 591.85 602.66 595.5C554.32 671.17 505.97 746.83 457.62 822.5C447.01 823.3 436.14 822.58 425.5 822.52C410.5 822.45 395.5 822.62 380.5 822.57C370.91 822.54 350.81 824.21 342.58 821.5C387.37 753.83 432.15 686.17 476.94 618.5C469.52 611.64 460.15 606.62 452.29 600.19C433.87 585.14 417 566.91 405.52 545.99C392.5 522.27 385.35 497.63 384.77 470.5C384.32 449.15 387.53 427.32 394.57 407.13C417.9 340.24 469.26 296.61 538.9 283.48C570.89 277.46 604.12 279.4 636.5 279.31C652.83 279.27 669.17 279.47 685.5 279.41C696.1 279.37 706.92 278.62 717.48 279.5ZM115.75 706.41C135.04 703.33 156.48 716.15 160.55 735.87C161.86 742.25 161.33 749.02 161.38 755.5C161.46 766.5 161.54 777.5 161.46 788.5C161.23 818.49 161.68 848.5 161.69 878.5C161.69 888.11 160.1 910.1 163.36 918.2C164.72 921.57 167.51 924.91 170.41 927.07C177.53 932.38 186.11 931.57 194.5 931.44C206.83 931.25 219.17 931.26 231.5 931.22C263.83 931.12 296.17 931.26 328.5 931.16C347.33 931.1 366.7 929.84 385.48 931.29C413.69 933.46 428.41 968.41 409.24 989.75C397.98 1002.28 382 1001.51 366.5 1001.3C343.18 1000.97 319.83 1001.07 296.5 1001.12C264.17 1001.2 231.83 1000.98 199.5 1001.06C180.25 1001.1 159.58 1003.14 140.71 998.66C122.43 994.32 106.54 981.8 95.61 966.86C78.62 943.65 80.71 918.66 80.76 891.5C80.84 849.5 82.33 807.49 82.15 765.5C82.11 756.68 81.15 747 82.61 738.29C85.39 721.72 99.32 709.04 115.75 706.41ZM968.68 706.38C989.07 703.36 1010.57 717.78 1013.84 738.53C1014.86 745.02 1014.33 751.94 1014.33 758.5C1014.34 769.17 1014.46 779.83 1014.39 790.5C1014.2 818.81 1014.88 847.18 1015.01 875.5C1015.12 901.42 1018.65 929.7 1007.42 953.9C996.88 976.62 974.96 994.5 950.42 999.73C932.62 1003.53 913.58 1001.57 895.5 1001.47C864.86 1001.31 834.14 1001.24 803.5 1001.58C779.17 1001.85 754.83 1001.38 730.5 1001.78C716.53 1002 700.84 1003.05 689.93 992.58C667.67 971.23 680.18 934.86 710.63 931.36C717.12 930.62 723.97 931.21 730.5 931.26C740.83 931.35 751.17 931.28 761.5 931.34C796.16 931.55 830.84 932.09 865.5 931.92C877.5 931.86 889.5 931.97 901.5 932C909.82 932.02 917.65 932.49 924.52 927.03C933.44 919.93 932.49 908.65 932.46 898.5C932.43 891.16 932.81 883.84 932.8 876.5C932.73 845.5 933.19 814.5 933.47 783.5C933.57 773.16 933.54 762.83 933.71 752.5C933.8 747.53 933.35 742.5 934.28 737.59C937.49 720.54 952.1 708.84 968.68 706.38Z" fill="currentColor" fill-rule="evenodd" stroke="currentColor" stroke-width="0.25" stroke-linejoin="round"/>
    </svg>""",

    "pen": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>
        <path d="m15 5 4 4"/>
    </svg>""",

    "line": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="4" y1="20" x2="20" y2="4"/>
    </svg>""",

    "arrow": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="5" y1="19" x2="19" y2="5"/>
        <polyline points="9 5 19 5 19 15"/>
    </svg>""",

    "rect": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="18" height="18" x="3" y="3" rx="2"/>
    </svg>""",

    "filled_rect": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="18" height="18" x="3" y="3" rx="2" fill="currentColor" fill-opacity="0.3"/>
    </svg>""",

    "circle": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="9"/>
    </svg>""",

    "shapes": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="3" width="11" height="11" rx="2"/>
        <circle cx="15.5" cy="15.5" r="5.5"/>
    </svg>""",

    "mask": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 4h5M15 4h5M4 20h5M15 20h5M4 4v5M20 4v5M4 15v5M20 15v5" stroke-dasharray="3 2"/>
        <path d="M8 15c2-7 6-7 8-3"/>
    </svg>""",

    "bold": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M6 4h8a4 4 0 0 1 4 4 4 4 0 0 1-4 4H6z"/>
        <path d="M6 12h9a4 4 0 0 1 4 4 4 4 0 0 1-4 4H6z"/>
    </svg>""",

    "underline": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M6 3v7a6 6 0 0 0 12 0V3"/>
        <line x1="4" y1="21" x2="20" y2="21"/>
    </svg>""",

    "italic": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="19" y1="4" x2="10" y2="4"/>
        <line x1="14" y1="20" x2="5" y2="20"/>
        <line x1="15" y1="4" x2="9" y2="20"/>
    </svg>""",

    "gradient": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="18" height="18" x="3" y="3" rx="2"/>
        <path d="M3 9l6-6"/>
        <path d="M3 15l12-12"/>
        <path d="M3 21l18-18"/>
        <path d="M9 21l12-12"/>
        <path d="M15 21l6-6"/>
    </svg>""",

    "highlighter": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="m9 11-6 6v3h9l3-3"/>
        <path d="m22 12-4.6 4.6a2.78 2.78 0 0 1-3.9 0l-4.1-4.1a2.78 2.78 0 0 1 0-3.9L14 4"/>
    </svg>""",

    "text": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="4 7 4 4 20 4 20 7"/>
        <line x1="9" y1="20" x2="15" y2="20"/>
        <line x1="12" y1="4" x2="12" y2="20"/>
    </svg>""",

    "mosaic": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="18" height="18" x="3" y="3" rx="2"/>
        <path d="M3 9h18"/>
        <path d="M3 15h18"/>
        <path d="M9 3v18"/>
        <path d="M15 3v18"/>
    </svg>""",

    "blur": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10" stroke-dasharray="2 2"/>
        <circle cx="12" cy="12" r="6" stroke-dasharray="2 2"/>
        <circle cx="12" cy="12" r="2" fill="currentColor"/>
    </svg>""",

    "grayscale": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <path d="M12 2a10 10 0 0 1 0 20z" fill="currentColor"/>
    </svg>""",

    "invert": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 2v20"/>
        <path d="M12 2a10 10 0 0 1 0 20z" fill="currentColor" fill-opacity="0.4"/>
        <circle cx="12" cy="12" r="10"/>
    </svg>""",

    "vibrant": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="4"/>
        <path d="M12 2v2"/>
        <path d="M12 20v2"/>
        <path d="m4.93 4.93 1.41 1.41"/>
        <path d="m17.66 17.66 1.41 1.41"/>
        <path d="M2 12h2"/>
        <path d="M20 12h2"/>
        <path d="m6.34 17.66-1.41 1.41"/>
        <path d="m19.07 4.93-1.41 1.41"/>
    </svg>""",

    "sepia": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="18" height="18" x="3" y="3" rx="2"/>
        <circle cx="9" cy="9" r="2"/>
        <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>
    </svg>""",

    "palette": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="13.5" cy="6.5" r=".5" fill="currentColor"/>
        <circle cx="17.5" cy="10.5" r=".5" fill="currentColor"/>
        <circle cx="8.5" cy="7.5" r=".5" fill="currentColor"/>
        <circle cx="6.5" cy="12.5" r=".5" fill="currentColor"/>
        <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/>
    </svg>""",

    "pipette": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="m14 7 3 3"/>
        <path d="m5 19 4-4"/>
        <path d="M19 5a2.83 2.83 0 0 0-4 0l-9.5 9.5a1 1 0 0 0-.25.45l-1.2 4.8a.5.5 0 0 0 .6.6l4.8-1.2a1 1 0 0 0 .45-.25L19 5Z"/>
    </svg>""",

    "undo": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M9 14 4 9l5-5"/>
        <path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5v0a5.5 5.5 0 0 1-5.5 5.5H11"/>
    </svg>""",

    "redo": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="m15 14 5-5-5-5"/>
        <path d="M20 9H9.5A5.5 5.5 0 0 0 4 14.5v0A5.5 5.5 0 0 0 9.5 20H13"/>
    </svg>""",

    "layers": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="12 2 2 7 12 12 22 7 12 2"/>
        <polyline points="2 17 12 22 22 17"/>
        <polyline points="2 12 12 17 22 12"/>
    </svg>""",

    "history": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
        <path d="M3 3v5h5"/>
        <path d="M12 7v5l4 2"/>
    </svg>""",

    # --- Действия (Нижняя панель) ---
    "save": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>
        <path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/>
        <path d="M7 3v4a1 1 0 0 0 1 1h7"/>
    </svg>""",

    "copy": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>
        <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>
    </svg>""",

    "search": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="8"/>
        <path d="m21 21-4.3-4.3"/>
    </svg>""",

    "video": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="m22 8-6 4 6 4V8Z"/>
        <rect width="14" height="12" x="2" y="6" rx="2" ry="2"/>
    </svg>""",

    "gif": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="2" y="3.5" width="20" height="17" rx="3.5"/>
        <path d="M8.5 8.5H6.5a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2h2v-3H6.5"/>
        <line x1="12" y1="8.5" x2="12" y2="15.5"/>
        <path d="M15.5 15.5v-7h4M15.5 12h3"/>
    </svg>""",

    "filter": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="m15 4-2 4-4 2 4 2 2 4 2-4 4-2-4-2z"/>
        <path d="M20 16l-1 2-2 1 2 1 1 2 1-2 2-1-2-1z"/>
        <path d="M6 15l-1 2-2 1 2 1 1 2 1-2 2-1-2-1z"/>
    </svg>""",

    "lock": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="18" height="11" x="3" y="11" rx="2" ry="2"/>
        <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
    </svg>""",

    "unlock": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="18" height="11" x="3" y="11" rx="2" ry="2"/>
        <path d="M7 11V7a5 5 0 0 1 9.9-1"/>
    </svg>""",

    "dynamic_bg": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="20" height="14" x="2" y="3" rx="2"/>
        <line x1="8" x2="16" y1="21" y2="21"/>
        <line x1="12" x2="12" y1="17" y2="21"/>
        <polygon points="10 8 15 11 10 14 10 8" fill="currentColor"/>
    </svg>""",

    "passthrough": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="18" height="18" x="3" y="3" rx="2" stroke-dasharray="3 3"/>
        <path d="m3 3 7.07 16.97 2.51-7.39 7.39-2.51L3 3z"/>
    </svg>""",

    "settings": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>
        <circle cx="12" cy="12" r="3"/>
    </svg>""",

    "add_region": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <line x1="12" y1="5" x2="12" y2="19"/>
        <line x1="5" y1="12" x2="19" y2="12"/>
    </svg>""",

    "close": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M18 6 6 18"/>
        <path d="m6 6 12 12"/>
    </svg>""",

    "trash": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 6h18"/>
        <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>
        <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>
        <line x1="10" y1="11" x2="10" y2="17"/>
        <line x1="14" y1="11" x2="14" y2="17"/>
    </svg>""",

    "edit": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
    </svg>""",

    "bring_front": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="12 2 2 7 12 12 22 7 12 2"/>
        <polyline points="2 17 12 22 22 17"/>
        <polyline points="2 12 12 17 22 12"/>
        <path d="M12 22V12"/>
        <path d="M8 16l4-4 4 4"/>
    </svg>""",

    "send_back": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="12 2 2 7 12 12 22 7 12 2"/>
        <polyline points="2 17 12 22 22 17"/>
        <polyline points="2 12 12 17 22 12"/>
        <path d="M12 12v10"/>
        <path d="M8 18l4 4 4-4"/>
    </svg>""",

    # --- Стили стрелок (Всплывающие опции) ---
    "arrow_classic": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="5" y1="19" x2="19" y2="5"/>
        <polygon points="12 5 19 5 19 12" fill="currentColor"/>
    </svg>""",

    "arrow_barbed": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="5" y1="19" x2="19" y2="5"/>
        <polyline points="12 5 19 5 19 12"/>
    </svg>""",

    "arrow_double": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="6" y1="18" x2="18" y2="6"/>
        <polygon points="11 5 19 5 19 13" fill="currentColor"/>
        <polygon points="5 11 5 19 13 19" fill="currentColor"/>
    </svg>""",

    "arrow_stealth": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="5" y1="19" x2="15" y2="9"/>
        <polygon points="11 5 19 5 19 13 16 11 11 16 13 11" fill="currentColor"/>
    </svg>""",

    "arrow_dashed": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="5" y1="19" x2="19" y2="5" stroke-dasharray="3 3"/>
        <polygon points="12 5 19 5 19 12" fill="currentColor"/>
    </svg>""",

    # --- Иконки рамки записи и захвата окон ---
    "record": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
        <circle cx="12" cy="12" r="7"/>
    </svg>""",

    "pause": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor"/>
        <rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor"/>
    </svg>""",

    "play": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
        <polygon points="6 3 20 12 6 21 6 3"/>
    </svg>""",

    "stop": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
        <rect x="5" y="5" width="14" height="14" rx="2"/>
    </svg>""",

    "mic": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
        <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
        <line x1="12" x2="12" y1="19" y2="22"/>
    </svg>""",

    "speaker": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/>
        <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
        <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
    </svg>""",

    "window": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="20" height="16" x="2" y="4" rx="2"/>
        <path d="M2 9h20"/>
        <circle cx="5" cy="6.5" r="1" fill="currentColor"/>
        <circle cx="8" cy="6.5" r="1" fill="currentColor"/>
    </svg>""",

    "maximize": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M8 3H5a2 2 0 0 0-2 2v3"/>
        <path d="M21 8V5a2 2 0 0 0-2-2h-3"/>
        <path d="M3 16v3a2 2 0 0 0 2 2h3"/>
        <path d="M16 21h3a2 2 0 0 0 2-2v-3"/>
    </svg>""",

    "pin": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="12" y1="17" x2="12" y2="22"/>
        <path d="M5 17h14v-2l-2-2V5h1V3H6v2h1v8l-2 2v2z"/>
    </svg>""",

    "mic_off": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="2" y1="2" x2="22" y2="22"/>
        <path d="M18.89 13.23A7.12 7.12 0 0 0 19 12v-2"/>
        <path d="M5 10v2a7 7 0 0 0 12 5"/>
        <path d="M15 9.34V5a3 3 0 0 0-5.68-1.33"/>
        <path d="M9 9v3a3 3 0 0 0 5.12 2.12"/>
        <line x1="12" y1="19" x2="12" y2="22"/>
    </svg>""",

    "speaker_off": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="2" y1="2" x2="22" y2="22"/>
        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/>
        <line x1="22" y1="9" x2="16" y2="15"/>
        <line x1="16" y1="9" x2="22" y2="15"/>
    </svg>""",

    "scroll": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="16" height="18" x="4" y="3" rx="2"/>
        <path d="M12 7v8"/>
        <path d="m9 12 3 3 3-3"/>
        <path d="M8 21h8"/>
    </svg>""",

    "timer": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="10" x2="14" y1="2" y2="2"/>
        <line x1="12" x2="15" y1="14" y2="11"/>
        <circle cx="12" cy="14" r="8"/>
    </svg>""",

    "crosshair": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <line x1="22" y1="12" x2="18" y2="12"/>
        <line x1="6" y1="12" x2="2" y2="12"/>
        <line x1="12" y1="6" x2="12" y2="2"/>
        <line x1="12" y1="22" x2="12" y2="18"/>
    </svg>""",

    "eye": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>
        <circle cx="12" cy="12" r="3"/>
    </svg>""",

    "eye_off": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/>
        <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/>
        <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/>
        <line x1="2" y1="2" x2="22" y2="22"/>
    </svg>""",

    "folder": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/>
    </svg>""",

    "keyboard": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="20" height="16" x="2" y="4" rx="2"/>
        <path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M6 12h.01M18 12h.01M10 12h4"/>
    </svg>""",

    "camera": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/>
        <circle cx="12" cy="13" r="3"/>
    </svg>""",

    "help": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
        <line x1="12" y1="17" x2="12.01" y2="17"/>
    </svg>""",

    "video": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="23 7 16 12 23 17 23 7"/>
        <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
    </svg>""",

    "settings": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>
        <circle cx="12" cy="12" r="3"/>
    </svg>"""
}


def create_themed_icon(name: str, is_dark: bool = True, size: int = 16, custom_color: str = None) -> QIcon:
    """
    Генерирует высокочеткий векторный QIcon из SVG-строки с поддержкой Normal и Active состояний.
    Рендерится в разрешении size * 2 для кристальной четкости на экранах с любым Windows масштабированием.
    """
    raw_svg = SVG_ICONS.get(name, "")
    if not raw_svg:
        return QIcon()

    normal_color = custom_color or ("#d4d4d8" if is_dark else "#27272a")
    active_color = "#ffffff"
    icon = QIcon()

    # Обычное состояние (Normal)
    svg_norm = raw_svg.replace("currentColor", normal_color)
    r_norm = QSvgRenderer(QByteArray(svg_norm.encode("utf-8")))
    pm_norm = QPixmap(size * 2, size * 2)
    pm_norm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm_norm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r_norm.render(p)
    p.end()
    icon.addPixmap(pm_norm, QIcon.Mode.Normal, QIcon.State.Off)

    # Активное состояние при нажатии / выборе (Active / Checked)
    svg_act = raw_svg.replace("currentColor", active_color)
    r_act = QSvgRenderer(QByteArray(svg_act.encode("utf-8")))
    pm_act = QPixmap(size * 2, size * 2)
    pm_act.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm_act)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r_act.render(p)
    p.end()
    icon.addPixmap(pm_act, QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(pm_act, QIcon.Mode.Active, QIcon.State.On)

    return icon


def get_svg_pixmap(name: str, color: str = "#ffffff", size: int = 16) -> QPixmap:
    """Генерирует одиночный QPixmap из SVG с указанным цветом."""
    raw_svg = SVG_ICONS.get(name, "")
    if not raw_svg:
        return QPixmap()

    svg_str = raw_svg.replace("currentColor", color)
    r = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p)
    p.end()
    return pm


def create_style_preview_icon(name: str, is_dark: bool = True, size=(26, 18)) -> QIcon:
    """Генерирует наглядную векторную мини-иконку предварительного просмотра стиля фигуры для выпадающих списков."""
    w, h = size
    pix = QPixmap(w * 2, h * 2)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2.0, 2.0)

    col = QColor(240, 240, 240) if is_dark else QColor(30, 30, 30)
    pen = QPen(col, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    cy = h / 2.0

    if name == "arrow_classic":
        painter.drawLine(QPointF(2, cy), QPointF(w - 7, cy))
        painter.setBrush(QBrush(col))
        painter.setPen(Qt.PenStyle.NoPen)
        poly = QPolygonF([QPointF(w - 2, cy), QPointF(w - 9, cy - 5), QPointF(w - 9, cy + 5)])
        painter.drawPolygon(poly)
    elif name == "arrow_barbed":
        painter.drawLine(QPointF(2, cy), QPointF(w - 4, cy))
        painter.drawLine(QPointF(w - 4, cy), QPointF(w - 10, cy - 5))
        painter.drawLine(QPointF(w - 4, cy), QPointF(w - 10, cy + 5))
    elif name == "arrow_double":
        painter.drawLine(QPointF(7, cy), QPointF(w - 7, cy))
        painter.setBrush(QBrush(col))
        painter.setPen(Qt.PenStyle.NoPen)
        poly_r = QPolygonF([QPointF(w - 2, cy), QPointF(w - 8, cy - 4.5), QPointF(w - 8, cy + 4.5)])
        poly_l = QPolygonF([QPointF(2, cy), QPointF(8, cy - 4.5), QPointF(8, cy + 4.5)])
        painter.drawPolygon(poly_r)
        painter.drawPolygon(poly_l)
    elif name == "arrow_stealth":
        painter.drawLine(QPointF(2, cy), QPointF(w - 6, cy))
        painter.setBrush(QBrush(col))
        painter.setPen(Qt.PenStyle.NoPen)
        poly = QPolygonF([QPointF(w - 2, cy), QPointF(w - 10, cy - 5), QPointF(w - 7, cy), QPointF(w - 10, cy + 5)])
        painter.drawPolygon(poly)
    elif name == "arrow_dashed":
        d_pen = QPen(col, 2, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(d_pen)
        painter.drawLine(QPointF(2, cy), QPointF(w - 7, cy))
        painter.setBrush(QBrush(col))
        painter.setPen(Qt.PenStyle.NoPen)
        poly = QPolygonF([QPointF(w - 2, cy), QPointF(w - 9, cy - 5), QPointF(w - 9, cy + 5)])
        painter.drawPolygon(poly)
    elif name == "line_solid":
        painter.drawLine(QPointF(2, cy), QPointF(w - 2, cy))
    elif name == "line_dashed":
        d_pen = QPen(col, 2, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(d_pen)
        painter.drawLine(QPointF(2, cy), QPointF(w - 2, cy))
    elif name == "line_dotted":
        d_pen = QPen(col, 2.5, Qt.PenStyle.DotLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(d_pen)
        painter.drawLine(QPointF(3, cy), QPointF(w - 3, cy))
    elif name == "rect_sharp":
        painter.drawRect(QRectF(3, 3, w - 6, h - 6))
    elif name == "rect_rounded":
        painter.drawRoundedRect(QRectF(3, 3, w - 6, h - 6), 3, 3)

    painter.end()
    return QIcon(pix)


def create_tool_cursor(tool_name: str) -> QCursor:
    """
    Создает интуитивный, высококонтрастный курсор для каждого инструмента рисования:
    - text: нативный I-Beam ('рельса' ввода текста)
    - move: открытая ладонь OpenHand
    - shapes/line/arrow/rect/circle: точный перекрестный прицел CrossCursor
    - pen/highlighter/mosaic: стилизованный четкий пиксмап-курсор с острием в точке касания.
    """
    if tool_name == "text":
        return QCursor(Qt.CursorShape.IBeamCursor)
    if tool_name == "move":
        return QCursor(Qt.CursorShape.OpenHandCursor)
    if tool_name in ("select", "cursor"):
        return QCursor(Qt.CursorShape.ArrowCursor)
    if tool_name in ("shapes", "line", "arrow", "rect", "circle", "filled_rect"):
        return QCursor(Qt.CursorShape.CrossCursor)

    size = 24
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    if tool_name == "pen":
        # Четкий стилизованный карандаш с острием в нижнем левом углу (1, 22)
        poly = QPolygonF([
            QPointF(1, 22),   # Острие карандаша
            QPointF(6, 17),
            QPointF(19, 4),
            QPointF(22, 7),
            QPointF(9, 20),
            QPointF(1, 22)
        ])
        p.setPen(QPen(QColor(0, 0, 0, 240), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawPolygon(poly)
        # Цветной грифель на острие
        tip_poly = QPolygonF([QPointF(1, 22), QPointF(5, 18), QPointF(7, 20)])
        p.setBrush(QColor(239, 68, 68))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(tip_poly)
        p.end()
        return QCursor(pix, 1, 22)

    elif tool_name == "highlighter":
        # Маркер со скошенным наконечником в (2, 22)
        poly = QPolygonF([
            QPointF(2, 22),
            QPointF(7, 17),
            QPointF(18, 6),
            QPointF(22, 10),
            QPointF(11, 21),
            QPointF(2, 22)
        ])
        p.setPen(QPen(QColor(0, 0, 0, 240), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawPolygon(poly)
        tip_poly = QPolygonF([QPointF(2, 22), QPointF(6, 18), QPointF(9, 21)])
        p.setBrush(QColor(234, 179, 8))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(tip_poly)
        p.end()
        return QCursor(pix, 2, 22)

    elif tool_name == "mosaic":
        # Прицел с шахматной сеткой цензуры по центру (9, 9)
        p.setPen(QPen(QColor(0, 0, 0, 230), 1.5))
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawRect(2, 2, 14, 14)
        p.fillRect(2, 2, 7, 7, QColor(0, 0, 0, 180))
        p.fillRect(9, 9, 7, 7, QColor(0, 0, 0, 180))
        p.fillRect(9, 2, 7, 7, QColor(255, 255, 255, 220))
        p.fillRect(2, 9, 7, 7, QColor(255, 255, 255, 220))
        p.end()
        return QCursor(pix, 9, 9)

    elif tool_name in ("pipette", "eyedropper"):
        # Пипетка с острием в нижнем левом углу (2, 22)
        poly = QPolygonF([
            QPointF(2, 22),
            QPointF(5, 18),
            QPointF(14, 9),
            QPointF(17, 6),
            QPointF(20, 9),
            QPointF(17, 12),
            QPointF(8, 21),
            QPointF(2, 22)
        ])
        p.setPen(QPen(QColor(0, 0, 0, 240), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(QColor(255, 255, 255, 240))
        p.drawPolygon(poly)
        tip_poly = QPolygonF([QPointF(2, 22), QPointF(4, 19), QPointF(6, 21)])
        p.setBrush(QColor(56, 189, 248))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(tip_poly)
        p.end()
        return QCursor(pix, 2, 22)

    p.end()
    return QCursor(Qt.CursorShape.CrossCursor)

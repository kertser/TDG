from backend.schemas.order import MessageClassification, OrderType
from backend.services.order_parser import order_parser


def test_tactical_command_matrix_ru_en():
    cases = [
        (
            "Mortar, stand by for fire support on request at the bridge crossing.",
            MessageClassification.command,
            OrderType.observe,
        ),
        (
            "Recon section, observe the eastern bridge and report all movement.",
            MessageClassification.command,
            OrderType.observe,
        ),
        (
            "Combat engineers, breach the roadblock at E6-3 and open a lane.",
            MessageClassification.command,
            OrderType.breach,
        ),
        (
            "Engineer section, lay mines on the northern road approach.",
            MessageClassification.command,
            OrderType.lay_mines,
        ),
        (
            "Mortar, put smoke on the bridge crossing at E6-2.",
            MessageClassification.command,
            OrderType.fire,
        ),
        (
            "B-squad, request smoke on the crossing and move under concealment.",
            MessageClassification.command,
            OrderType.request_fire,
        ),
        (
            "Сапёры, разверните мостоукладчик у переправы и наведите мост.",
            MessageClassification.command,
            OrderType.deploy_bridge,
        ),
        (
            "Construction engineers, build a command post near Hill 170.",
            MessageClassification.command,
            OrderType.construct,
        ),
        (
            "Logistics unit, resupply A-squad and stay behind them.",
            MessageClassification.command,
            OrderType.resupply,
        ),
        (
            "БПЛА, прикрой правый фланг наблюдением и докладывай о противнике.",
            MessageClassification.command,
            OrderType.observe,
        ),
        (
            "Aviation flight, insert recon team to Hill 201.",
            MessageClassification.command,
            OrderType.move,
        ),
        (
            "A-squad, split off one third of your strength to screen the bunker.",
            MessageClassification.command,
            OrderType.split,
        ),
        (
            "B-squad, merge with C-squad and continue as one element.",
            MessageClassification.command,
            OrderType.merge,
        ),
        (
            "AAA -> C-squad: Какая дистанция до ближайшей дороги?",
            MessageClassification.status_request,
            OrderType.report_status,
        ),
    ]

    for text, expected_classification, expected_order_type in cases:
        parsed = order_parser._fallback_parse(text)
        assert parsed.classification == expected_classification, text
        assert parsed.order_type == expected_order_type, text

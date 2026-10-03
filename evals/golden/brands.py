"""Three fictional brands for evals: a B2B manufacturer, a D2C brand and a SaaS tool.

These companies, their customers and their numbers are invented. Within the evals the brain
is the ground truth: a piece may use the proof listed here and nothing else.
"""

from growthcrew.agents.research_models import (
    Learning,
    MarketSignals,
    Quote,
    QuoteTheme,
    ResearchBrief,
    ResearchReport,
    VoiceOfCustomer,
)
from growthcrew.brain.models import (
    FIELD_PATHS,
    ICP,
    Brain,
    BrandVoice,
    BusinessProfile,
    Competitor,
    FieldMeta,
    Product,
    Proof,
    ProofItem,
    ToneSliders,
    VoiceGuide,
)
from growthcrew.schemas import Claim


def _brain(workspace: str, **parts) -> Brain:
    confirmed = FieldMeta(status="confirmed", confidence="high", note="eval fixture")
    return Brain(
        workspace=workspace, version=1, fields=dict.fromkeys(FIELD_PATHS, confirmed), **parts
    )


def _proof(summary: str, quote: str, attribution: str, url: str) -> ProofItem:
    return ProofItem(summary=summary, quote=quote, attribution=attribution, source_url=url)


NORTHFIELD = _brain(
    "eval-northfield",
    source_url="https://northfield.example",
    business=BusinessProfile(
        name="Northfield Precision",
        one_liner="CNC machining and should-cost engineering for industrial OEMs.",
        what_they_sell="Precision CNC-machined components in aluminium and steel, plus "
        "should-cost analysis that tells buyers what a part ought to cost before they quote.",
        pricing="Quote-based. Should-cost reports are free with a first production order.",
        geography="Ships from Pune, India to OEMs in Germany, the UK and the US.",
        stage="established",
    ),
    icp=ICP(
        audience_type="b2b",
        firmographics=[
            "Industrial and off-highway OEMs",
            "200 to 5,000 employees",
            "Buyer: sourcing manager or head of procurement",
        ],
        pains=[
            "Supplier quotes for the same part vary by 40% with no explanation",
            "Late deliveries stop the assembly line",
            "No in-house time to build should-cost models",
        ],
        goals=[
            "Cut bought-out part cost without changing the design",
            "Fewer, more reliable suppliers",
        ],
        buying_triggers=[
            "Annual cost-down target from finance",
            "A supplier misses a delivery",
            "New product introduction needing prototypes fast",
        ],
        objections=["Quality risk of an overseas supplier", "Tariffs cancel the saving"],
    ),
    voice=BrandVoice(
        tone=ToneSliders(formality=4, playfulness=1, enthusiasm=2, technicality=4),
        do_words=["should-cost", "tolerance", "landed cost", "first-article inspection"],
        dont_words=["cheap", "world-class"],
        example_passages=[
            "A quote tells you what a supplier wants to charge. A should-cost model tells you what the part ought to cost.",
            "We hold ±0.01 mm on production runs and send the inspection report with every shipment.",
            "If we cannot make the part for less than you pay today, we will tell you before you place an order.",
        ],
        guide=VoiceGuide(
            sentence_length="Average 14 words; no sentence over 25.",
            jargon_level="moderate",
            banned_phrases=["one-stop shop", "best-in-class"],
            rules=[
                "Write in first person plural (we).",
                "State a number or a tolerance instead of an adjective.",
                "No exclamation marks.",
                "Name the trade-off when there is one.",
            ],
        ),
        avg_sentence_words=14.0,
    ),
    products=[
        Product(
            name="CNC machining",
            description="3- and 5-axis milling and turning, 10 to 10,000 units.",
        ),
        Product(name="Should-cost analysis", description="Line-by-line cost model for a drawing."),
    ],
    proof=Proof(
        case_studies=[
            _proof(
                "Hydraulic manifold cost-down",
                "Northfield cut our manifold cost by 18% without a design change",
                "Head of Procurement, Brenner Hydraulik",
                "https://northfield.example/cases/brenner",
            )
        ],
        testimonials=[
            _proof(
                "On-time delivery",
                "Forty-one shipments, none late",
                "Sourcing Manager, Halden Off-Highway",
                "https://northfield.example/customers",
            )
        ],
        stats=[
            _proof(
                "Delivery record",
                "98.6% on-time delivery over the last 24 months",
                "Northfield delivery report",
                "https://northfield.example/quality",
            )
        ],
    ),
    competitors=[
        Competitor(name="Kestrel Machining", url="https://kestrel.example"),
        Competitor(name="ProtoWerk", url="https://protowerk.example"),
    ],
)

LOOMHOUSE = _brain(
    "eval-loomhouse",
    source_url="https://loomhouse.example",
    business=BusinessProfile(
        name="Loomhouse",
        one_liner="Stonewashed linen bedding, sold direct.",
        what_they_sell="Linen sheets, duvet covers and pillowcases woven from European flax.",
        pricing="Sheet sets from $189; duvet covers from $169. Free returns for 60 nights.",
        geography="Ships within the US and Canada.",
        stage="growth",
    ),
    icp=ICP(
        audience_type="b2c",
        demographics=[
            "Ages 28 to 45",
            "Homeowners and long-term renters",
            "Household income above $90k",
        ],
        pains=[
            "Cotton sheets sleep hot",
            "Cheap linen is scratchy and pills",
            "Hard to judge fabric online",
        ],
        goals=["Sleep cooler", "Buy bedding once that lasts for years"],
        buying_triggers=["Moving home", "Start of summer", "A wedding registry"],
        objections=["Linen wrinkles", "Price compared with cotton"],
    ),
    voice=BrandVoice(
        tone=ToneSliders(formality=2, playfulness=3, enthusiasm=3, technicality=2),
        do_words=["stonewashed", "flax", "lived-in", "cool to the touch"],
        dont_words=["luxury", "premium"],
        example_passages=[
            "Linen wrinkles. We think that is the point.",
            "Our flax is grown in Normandy and woven in Portugal. Then we wash it until it feels like it has been yours for years.",
            "Sleep on it for 60 nights. If you do not love it, send it back.",
        ],
        guide=VoiceGuide(
            sentence_length="Short. Average 10 words; fragments are fine.",
            jargon_level="none",
            banned_phrases=["treat yourself", "elevate your sleep"],
            rules=[
                "Write to one person (you).",
                "Describe how it feels, not how it is made, first.",
                "Admit the drawback (wrinkles, price) before the reader raises it.",
                "At most one exclamation mark per piece.",
            ],
        ),
        avg_sentence_words=10.0,
    ),
    products=[
        Product(
            name="Linen sheet set",
            description="Fitted sheet, flat sheet, two pillowcases.",
            price="$189",
        ),
        Product(name="Linen duvet cover", description="Button closure, corner ties.", price="$169"),
    ],
    proof=Proof(
        testimonials=[
            _proof(
                "Sleeps cooler",
                "First summer I have not kicked the covers off",
                "Maya R., verified buyer",
                "https://loomhouse.example/reviews",
            ),
            _proof(
                "Softness",
                "Softer after every wash, which I did not believe until the tenth",
                "Dan K., verified buyer",
                "https://loomhouse.example/reviews",
            ),
        ],
        stats=[
            _proof(
                "Review average",
                "4.8 out of 5 from 2,300 reviews",
                "Loomhouse reviews page",
                "https://loomhouse.example/reviews",
            )
        ],
    ),
    competitors=[
        Competitor(name="Brightside Linen", url="https://brightside.example"),
        Competitor(name="Cotton & Co", url="https://cottonco.example"),
    ],
)

LEDGERLY = _brain(
    "eval-ledgerly",
    source_url="https://ledgerly.example",
    business=BusinessProfile(
        name="Ledgerly",
        one_liner="Invoicing for freelancers that chases late payments for you.",
        what_they_sell="An invoicing web app with automatic payment reminders and card or bank payment links.",
        pricing="Free for 3 invoices a month. Pro is $12 a month, billed monthly.",
        geography="Available in the US, UK, Canada and Australia.",
        stage="early",
    ),
    icp=ICP(
        audience_type="b2b",
        firmographics=[
            "Solo freelancers and studios of up to 5 people",
            "Designers, developers, writers, consultants",
        ],
        pains=[
            "Chasing late invoices is awkward",
            "Spreadsheets lose track of who has paid",
            "Accounting suites are built for accountants",
        ],
        goals=["Get paid on time", "Spend under 10 minutes a week on invoicing"],
        buying_triggers=["A client pays 60 days late", "Tax season", "Going full-time freelance"],
        objections=["Another subscription", "Clients might find reminders rude"],
    ),
    voice=BrandVoice(
        tone=ToneSliders(formality=2, playfulness=3, enthusiasm=3, technicality=2),
        do_words=["get paid", "reminder", "on time", "invoice"],
        dont_words=["solution", "platform"],
        example_passages=[
            "You did the work. Asking to be paid for it should not be the hard part.",
            "Ledgerly sends the polite nudge on day 7, the firmer one on day 14, and tells you when the money lands.",
            "No accounting degree required. Send your first invoice in two minutes.",
        ],
        guide=VoiceGuide(
            sentence_length="Average 12 words.",
            jargon_level="light",
            banned_phrases=["streamline your workflow", "all-in-one"],
            rules=[
                "Write to one freelancer (you).",
                "Use contractions.",
                "Say what the product does in a verb, not a category noun.",
                "No finance promises: we send reminders, we do not guarantee payment.",
            ],
        ),
        avg_sentence_words=12.0,
    ),
    products=[
        Product(
            name="Ledgerly Free", description="3 invoices a month, manual reminders.", price="$0"
        ),
        Product(
            name="Ledgerly Pro",
            description="Unlimited invoices, automatic reminders, payment links.",
            price="$12 a month",
        ),
    ],
    proof=Proof(
        testimonials=[
            _proof(
                "Paid faster",
                "I stopped writing 'just following up' emails the week I signed up",
                "Priya S., freelance designer",
                "https://ledgerly.example/customers",
            )
        ],
        stats=[
            _proof(
                "Payment time",
                "Invoices sent with automatic reminders are paid 11 days sooner on average",
                "Ledgerly product data, 2026",
                "https://ledgerly.example/data",
            )
        ],
    ),
    competitors=[
        Competitor(name="BillBee", url="https://billbee.example"),
        Competitor(name="PaperTrail", url="https://papertrail.example"),
    ],
)

BRANDS: dict[str, Brain] = {"northfield": NORTHFIELD, "loomhouse": LOOMHOUSE, "ledgerly": LEDGERLY}


def research_fixture(brand: Brain) -> ResearchReport:
    """A small research report built from the brain, so the strategist has cited findings."""
    home = brand.source_url
    rival = brand.competitors[0]
    proof = [*brand.proof.case_studies, *brand.proof.testimonials, *brand.proof.stats]
    quotes = [
        Quote(text=item.quote, source_url=item.source_url) for item in brand.proof.testimonials
    ]
    return ResearchReport(
        workspace=brand.workspace,
        focus="eval fixture",
        teardowns=[],
        voice_of_customer=VoiceOfCustomer(
            top_pains=[
                Claim(statement=pain, source_url=f"{home}/customers") for pain in brand.icp.pains
            ],
            desired_outcomes=[
                Claim(statement=goal, source_url=f"{home}/customers") for goal in brand.icp.goals
            ],
            objections=[
                Claim(statement=item, source_url=f"{home}/faq") for item in brand.icp.objections
            ],
            themes=[QuoteTheme(theme="What customers praise", quotes=quotes)] if quotes else [],
        ),
        market_signals=MarketSignals(
            trends=[
                Claim(
                    statement=f"{rival.name} positions on price and breadth of range",
                    source_url=rival.url,
                )
            ],
            seasonality=[
                Claim(statement=f"Demand rises around: {trigger}", source_url=f"{home}/blog")
                for trigger in brand.icp.buying_triggers[:2]
            ],
            search_demand_themes=[],
        ),
        brief=ResearchBrief(
            headline=f"What we know about {brand.business.name}'s market",
            learnings=[
                Learning(
                    insight=f"The sharpest customer pain is: {brand.icp.pains[0]}",
                    why_it_matters="It should lead the messaging",
                    confidence="high",
                    source_urls=[f"{home}/customers"],
                ),
                Learning(
                    insight=f"Strongest proof available: {proof[0].summary}",
                    why_it_matters="It is the only claim we can substantiate today",
                    confidence="high",
                    source_urls=[proof[0].source_url],
                ),
                Learning(
                    insight=f"Main objection: {brand.icp.objections[0]}",
                    why_it_matters="Content must answer it early",
                    confidence="medium",
                    source_urls=[f"{home}/faq"],
                ),
            ],
            open_questions=["Which channel do buyers use first?"],
        ),
    )

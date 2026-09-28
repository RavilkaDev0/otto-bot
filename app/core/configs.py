"""Application settings loaded from environment and optional `.env` file."""

from typing import ClassVar

from pydantic import HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.schemas.enums import Controller
from app.schemas.product import Address, Compliance, ProductSafety


class Settings(BaseSettings):
    """Typed configuration container for API, OTTO auth, and database settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    otto_jv_client_id: str
    otto_jv_client_secret: str

    otto_xl_client_id: str
    otto_xl_client_secret: str

    otto_base_url: str = "https://api.otto.market"
    otto_scope: str = "orders products availability shipping-profiles"
    otto_timeout_seconds: float = 20.0

    # DATABASE
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_pass: str
    sqlalchemy_echo: bool = False

    # JWT settings
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1200

    # Afterbuy
    afterbuy_base_url: str = "https://aftercool.de"
    afterbuy_timeout_seconds: float = 30.0
    afterbuy_username: str
    afterbuy_password: str
    redis_url: str = "redis://127.0.0.1:6379/0"
    mongodb_url: str | None = None
    mongodb_database: str = "otto_bot"
    mongodb_factory_tasks_collection: str = "factory_task_states"
    generated_media_root: str = "storage/generated"
    generated_media_url_prefix: str = "/generated-media"
    openai_api_key: str | None = None
    openai_image_model: str = "gpt-image-2"
    openai_image_fallback_model: str = "gpt-image-1"
    openai_image_size: str = "1536x1024"
    openai_image_edit_size: str = "auto"
    openai_image_quality: str = "high"
    openai_image_background: str = "opaque"
    openai_image_output_format: str = "jpeg"
    openai_image_timeout_seconds: float = 180.0
    otto_image_max_side: int = 4500
    otto_image_min_width: int = 500
    otto_image_min_height: int = 1000
    otto_image_target_max_bytes: int = 5 * 1024 * 1024
    otto_allowed_image_formats: str = "jpg,png"

    # ARQ settings
    arq_redis_url: str | None = None
    arq_queue_name: str = "arq:otto-bot"
    arq_worker_concurrency: int = 2
    arq_job_timeout_seconds: int = 7200
    arq_job_expires_seconds: int = 14 * 24 * 60 * 60
    factory_product_concurrency: int = 10
    factory_ai_enrich_item_timeout_seconds: int = 300
    factory_ai_enrich_chunk_size: int = 50
    factory_submit_preview_enabled: bool = True

    # Sentry
    sentry_dsn: str | None = None
    sentry_environment: str = "development"
    sentry_release: str | None = None
    sentry_traces_sample_rate: float = 0.1

    gpt_key: str
    openai_attribute_fill_api_keys: str | None = None
    deepl_api_key_test: str | None = None
    deepl_url: str = "https://api-free.deepl.com"
    deepl_timeout_seconds: float = 12.0
    deepl_min_interval_seconds: float = 1.25
    deepl_max_retries: int = 6
    deepl_retry_base_delay_seconds: float = 2.0
    
    # Rabbitmq
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_app_user: str
    rabbitmq_app_password: str
    rabbitmq_virtual_host: str
    rabbitmq_queue_name: str

    compliance: ClassVar = {
        Controller.JV: Compliance(
            productSafety=ProductSafety(
                addresses=[
                    Address(
                        name="AEA GmbH & Co. KG",
                        address="Am Flugplatz 28, 88483 Burgrieden",
                        email="info@jvmoebel.de",
                        phone="07392 - 93 78 44 0",
                        url=HttpUrl("https://www.jvmoebel.de/Infos/Kontakt.htm"),
                    )
                ]
            )
        ),
        Controller.XL: Compliance(
            productSafety=ProductSafety(
                addresses=[
                    Address(
                        name="XL MOEBEL GmbH",
                        address="Am Flugplatz 26, 88483 Burgrieden",
                        email="info@xlmoebel.de",
                        phone="07392 - 93 78 188",
                        url=HttpUrl("https://www.xlmoebel.de/kontakt"),
                    )
                ]
            )
        ),
    }

    # Constants
    CATEGORIES: list = [
        "Bild",
        "LED Kronleuchter",
        "Kleiderschrank",
        "Eckdusche",
        "Kindertisch",
        "3D Wandpaneel",
        "Laptoptisch",
        "Schaukelstuhl",
        "Gartenhaus-Fußboden",
        "Kinderstuhl",
        "Hängesessel",
        "Gartenliege",
        "Polsterbank",
        "Regal",
        "Babyschaukel",
        "Autobett",
        "Wandspiegel",
        "Dekofigur",
        "Stehpult",
        "Bettanlage",
        "Wohnlandschaft",
        "Ohrensessel",
        "Teichleuchte",
        "Zaundoppeltür",
        "Lattenrost",
        "Mediawand",
        "Raumteiler",
        "Highboard",
        "Tischvase",
        "TV-Wand",
        "Hängevitrine",
        "Haustür",
        "Kunstlederbett",
        "Bierkrug",
        "Spielhaus (Spielhäuser)",
        "Säulen-Esstisch",
        "Jugendbett",
        "Nachttischlampe",
        "Gartenlounge-Set",
        "Couchtisch",
        "LED Deckenstrahler",
        "Holzstuhl",
        "TV-Bank",
        "Kronleuchter",
        "Küchenbuffet",
        "Schirmständer",
        "Spielzeugtruhe",
        "Lernspielzeug",
        "Eckkleiderschrank",
        "Mehrkindschaukel",
        "Relaxsessel",
        "Konsole",
        "4-Sitzer",
        "Sessel",
        "Staffelei",
        "Dekorpaneele",
        "Agility-Hürde",
        "Buchstütze",
        "Buffetaufsatz",
        "Flaschenregal",
        "Bistrotisch",
        "Schreibtischlampe",
        "Outdoor-Küche",
        "LED Wandleuchte",
        "Dekokamin",
        "Schminktisch",
        "Ölbild",
        "Außen-Stehlampe",
        "Arbeitsplatte",
        "Badezimmerspiegelschrank",
        "Kompaktgarderobe",
        "Power Rack",
        "Gamingtisch",
        "Bonnellfederkernmatratze",
        "Kindersessel",
        "Wanddekoobjekt",
        "Kugelleuchte",
        "Blumentopf",
        "Hochbett",
        "Rechteckduschwanne",
        "Stepper",
        "Einzelschaukel",
        "Bauchnabelpiercing",
        "XXL-Sessel",
        "Wasserspiel",
        "Gartensessel",
        "Eckkommode",
        "Sitztruhe",
        "Schubkastenkommode",
        "Küchentisch",
        "Tischkerzenhalter",
        "Regal-Schreibtisch",
        "TV-Board",
        "Essgruppe",
        "Kletterwand",
        "Multimediasessel",
        "Klettergerüst",
        "Eckunterschrank",
        "Empfangstheke",
        "Polstergarnitur",
        "Schienensystem-Leuchten",
        "Nachtkommode",
        "Wandregal",
        "Kinderregal",
        "Crossrack",
        "Spiegelleuchte",
        "Schale",
        "Bodenvase",
        "Hängeleuchten",
        "Pouf",
        "Wandregaltisch",
        "Dekovase",
        "Beistelltisch",
        "Kraftstation",
        "Gartenhaus",
        "Nestschaukel",
        "Spiel",
        "Sitzhocker",
        "Medizinschrank",
        "Latexmatratze",
        "Pendelleuchte",
        "Deckenleuchte",
        "Sitzbank",
        "Badkommode",
        "Eckregal",
        "Atelierstaffelei",
        "Schuhregal",
        "Brandschutztür",
        "2-Sitzer",
        "Wandhaken",
        "Spieltisch",
        "Multitrainer",
        "Boxspringbett",
        "Regalelement",
        "Badewanne",
        "Kopfkissen",
        "Bodenfliese",
        "Bilderrahmen",
        "Matratzenauflage",
        "WLAN-Adapter",
        "Rollcontainer",
        "Kombikommode",
        "Wohnzimmertisch",
        "Trittschalldämmfolie",
        "Kleiderständer",
        "Esszimmer-Set",
        "Drehsessel",
        "Zierleiste",
        "Massagesessel",
        "Konsolentisch",
        "Tischtennisplatte",
        "Gartentor",
        "Gartenlounge-Hocker",
        "Strickmaschinen-Doppelbett",
        "Chaiselongue",
        "Kinderschreibtisch",
        "Garderobenhaken",
        "LED Dekoobjekt",
        "Dekospiegel",
        "Schreibtisch",
        "Kerzenleuchter",
        "Falteimer",
        "Engelfigur",
        "Lowboard",
        "Allzweckkiste",
        "Satztisch",
        "Polsterstuhl",
        "Relaxliege",
        "Schaukelsitz",
        "Weinschrank",
        "Zimmertür",
        "Boxspringmatratze",
        "Big-Sofa",
        "Stauraumvitrine",
        "LED Nachtlicht",
        "Holzbett",
        "Spielturm",
        "Taschenfederkernmatratze",
        "Hängeleuchte",
        "Wippe",
        "Handmuskeltrainer",
        "Nachtschrank",
        "Kinderkleiderschrank",
        "Wohnzimmer-Set",
        "Digitalpiano",
        "Bartisch",
        "Küchenzeile",
        "Weihnachtsfigur",
        "Gartenzwerg",
        "Whirlpool",
        "Wasserbett",
        "Arbeitstisch",
        "Container",
        "Massivholzbett",
        "Bücherregal",
        "Kinderbett",
        "Saunahaus",
        "Hochschrank",
        "Stehlampe",
        "Bürostuhl",
        "Kosmetikspiegel",
        "Teppich",
        "Streaming-Stick",
        "Eckschrank",
        "Hängeregal",
        "Air-Hockeytisch",
        "Gartensockel",
        "TV-Schrank",
        "Aufsatzschrank",
        "CD-Regal",
        "Loungeset",
        "Essbank",
        "Tierfigur",
        "Mörtel",
        "Schaukelsessel",
        "Schrankbett",
        "Dekokissen",
        "Gleichgewichtstrainer",
        "Küchenspüle",
        "Gartenleuchte",
        "Schwebetürenschrank",
        "Topper",
        "Ölgemälde",
        "Gartenfigur",
        "Federkernmatratze",
        "Badmöbel-Set",
        "Wandboard",
        "Küchenstuhl",
        "Poolwasserfall",
        "Garderobenständer",
        "Esstisch",
        "Glasvitrine",
        "Eckwanne",
        "Schlafzimmer-Set",
        "Wohnwand",
        "Spiegelschrank",
        "Truhe",
        "Hängematte",
        "Dekokugel",
        "Hängeschrank",
        "Wandtafel",
        "Fußbad",
        "Esszimmerstuhl",
        "Waschbeckenschrank",
        "Eckschreibtisch",
        "Recamiere",
        "Doppelwaschtisch",
        "Dart-Wandschutz",
        "Ottomane",
        "Stuhl",
        "Küchenregal",
        "Loungesofa",
        "Visko-Kissen",
        "Himmelbett",
        "Komplettschlafzimmer",
        "Standregal",
        "Pool-Abdeckplane",
        "Grillkamin",
        "Dampfdusche",
        "Babymöbel-Set",
        "Sauna",
        "Babybett",
        "Schuhbank",
        "Hollywoodschaukel",
        "Kletter-Trainingsgerät",
        "Rankgitter",
        "Chesterfield-Sofa",
        "Flügel Klavier",
        "Wandgarderobe",
        "Läufer",
        "Pflanzkübel",
        "WLAN-Router",
        "Gartenbrunnen",
        "Polsterbett",
        "Aktenregal",
        "Lederbett",
        "Tischleuchte",
        "Wandbild",
        "Doppelwaschbecken",
        "Dekoobjekt",
        "Blumenkasten",
        "Mehrzweckschrank",
        "Fußtrainer",
        "Deko-Wandregal",
        "Vitrine",
        "Aktenschrank",
        "Armlehnstuhl",
        "Hundehütte",
        "Schuhkipper",
        "Jugendzimmer-Set",
        "Standtafel",
        "Kerzenhalter",
        "Holzkiste",
        "Buffet",
        "Kleiderbügel",
        "Sofakissen",
        "Schaukelgestell",
        "Balancetrainer",
        "Schreibtischstuhl",
        "Anrichte",
        "Waschbecken",
        "Büro-Set",
        "Garderobenpaneel",
        "Unterschrank",
        "Grillkota",
        "Telefontisch",
        "Katzenhaus",
        "Bettbank",
        "Tierbett",
        "Bodenleuchte",
        "Hybridmatratze",
        "Bett",
        "Infrarotkabine",
        "Doppelschaukel",
        "Rückentrainer",
        "Aufsatzwaschbecken",
        "Raumteilerregal",
        "Standstaffelei",
        "Sitzgruppe",
        "Schrank-Set",
        "Trittsteine",
        "Outdoor-Küchenmodul",
        "Bücherwand",
        "Schranksystem",
        "Wanduhr",
        "Gemälde",
        "Seitenschrank",
        "Cocktailsessel",
        "Skulptur",
        "Faltstuhl",
        "Spielhaus",
        "Regenschirmhalter",
        "Vogeltränke",
        "Blumenständer",
        "Badregal",
        "Servierwagen",
        "Kindersofa",
        "Duschwanne",
        "Hochkommode",
        "Multimediabett",
        "Waschbeckenunterschrank",
        "Gartentisch",
        "Fußbodenheizung",
        "Spiegel",
        "Kaltschaummatratze",
        "Deko-Windrad",
        "Waschtisch",
        "Chesterfield-Sessel",
        "Eckhängeschrank",
        "Wandhängeschrank",
        "Eckbank",
        "Badhocker",
        "Garderoben-Set",
        "USB-Stehleuchte",
        "Komfortschaummatratze",
        "Eckvitrine",
        "Truhenbank",
        "Hockerbank",
        "Zierbrunnen",
        "Frontblende",
        "Mehrfach-Kleiderbügel",
        "Drehstuhl",
        "Garderobenschrank",
        "Gartenlounge-Bank",
        "Bauchtrainer",
        "Drehtürenschrank",
        "Einzelbett",
        "Präsentationsstaffelei",
        "Zimmerbrunnen",
        "Stauraumbett",
        "Küchensofa",
        "Sekretär",
        "Standspiegel",
        "Küchenwagen",
        "Stauraumschrank",
        "Gartenstuhl",
        "Esszimmersessel",
        "Standuhr",
        "Nachttisch",
        "Rutsche",
        "Gartenbank",
        "Tierhaus",
        "Chefsessel",
        "Garderobe",
        "Kunstdruck",
        "Schiebetürenschrank",
        "Zeitungsständer",
        "Weinregal",
        "Bettgestell",
        "Dekoschale",
        "Büromöbel-Set",
        "KOB Set-Artikel",
        "Hochstuhl",
        "Wandleuchte",
        "Whirlpool-Badewanne",
        "Fußhocker",
        "Kickertisch",
        "Etagenbett",
        "Schuhschrank",
        "Bar-Butler",
        "Federwippe",
        "Kerzenständer",
        "3-Sitzer",
        "Herrendiener",
        "Deckenleuchten",
        "Aufbewahrungsbox",
        "Babyzimmer-Komplettset",
        "Fasssauna",
        "Loungesessel",
        "Hocker",
        "TV-Ständer",
        "Bank",
        "Tafel",
        "Garten-Essgruppe",
        "Apothekerschrank",
        "Buddhafigur",
        "Kommode",
        "1,5-Sitzer",
        "Konferenztisch",
        "Sandkasten",
        "Hängestuhl",
        "Rundbett",
        "Ecksofa",
        "Designteppich",
        "Stehtisch",
        "Bilderleuchte",
        "Klapptisch",
        "TV-Regal",
        "Computertisch",
        "Billardtisch",
        "Badezimmer-Set",
        "Polsterhocker",
        "Trennwandplatten",
        "Glastisch",
        "Sideboard",
        "Stoff",
        "Barschrank",
        "Sofa",
        "TV-Sessel",
        '"2,5-Sitzer"',
        "Klimmzugstange",
        "Poolleiter",
        "Schlafsofa",
        "Wandpaneel",
        "Paravent",
        "Barista-Set",
        "Beistellschrank",
        "Bar-Set",
        "Papierkorb",
        "Nachtkonsole",
        "Gartensofa",
        "Barhocker",
        "Bauchmuskelmaschine",
        "Dekosäule",
    ]


settings = Settings()

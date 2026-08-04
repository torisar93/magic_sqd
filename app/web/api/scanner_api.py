"""Обёртка app/scanner.py для фронтенда — превращает dataclass-дерево
brand -> ModelGroup -> ModelInfo в JSON-совместимый словарь. ModelInfo.dir
(Path) отдаётся наружу как непрозрачная строка-ключ ("key") — фронтенд не
разбирает её, а просто передаёт обратно в install_api/car_editor_api/
admin_api, когда нужно что-то сделать с конкретной моделью (тот же принцип,
что self.current_model в старом gui.py, только без скрытого состояния на
стороне Python — какая модель выбрана, помнит JS)."""
import base64

from ..events import event_bridge
from ...scanner import scan_cars, scan_apks, ModelInfo

BRAND_LOGO_FILENAMES = ("logo.png", "logo.jpg", "logo.jpeg")
_LOGO_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def apk_to_dict(apk) -> dict:
    """Общий JSON-вид ApkInfo — используется и для общей библиотеки apk/
    (см. list_apks), и для "стандартных" APK конкретного этапа (см.
    install_api.standard_apks) — оба берут dataclass из одного и того же
    scanner.py."""
    return {
        "path": str(apk.path),
        "name": apk.name,
        "description": apk.description,
        "category": apk.category,
        "remote_only": apk.remote_only,
        "size": apk.size,
    }


class ScannerApi:
    def __init__(self, cars_dir, apk_dir):
        self.cars_dir = cars_dir
        self.apk_dir = apk_dir
        self._models_by_key: dict[str, ModelInfo] = {}

    def list_apks(self) -> list[dict]:
        return [apk_to_dict(apk) for apk in scan_apks(self.apk_dir)]

    def list_cars(self) -> dict:
        brands_tree = scan_cars(self.cars_dir)
        self._models_by_key = {}
        brands = []
        for brand, groups in brands_tree.items():
            brands.append({
                "name": brand,
                "logo": self._brand_logo_data_uri(brand),
                "groups": [self._group_to_dict(group) for group in groups],
            })
        return {"brands": brands}

    def get_model(self, key: str) -> ModelInfo | None:
        """Для других *_api.py (install_api, car_editor_api, ...), которым
        нужен настоящий ModelInfo, а не JSON — единый источник вместо того,
        чтобы каждый заново парсил cars/ по строке-ключу."""
        return self._models_by_key.get(key)

    def select_model(self, key: str) -> dict:
        """Тот же смысл, что _select_model() в старом gui.py: логирует
        предупреждение, если у модели нет stages.py, и отдаёт метаданные,
        которые правая панель использует, чтобы включить кнопки
        "Изменить машину"/"Сообщить о проблеме" (см. фазу 3+)."""
        model = self._models_by_key.get(key)
        if model is None:
            return {"error": "unknown model key"}
        if not model.stages_script:
            event_bridge.push({
                "kind": "log",
                "text": f"[{model.display_label}] Внимание: нет stages.py в {model.dir}",
            })
        return self._model_to_dict(model)

    def _group_to_dict(self, group) -> dict:
        return {
            "name": group.name,
            "has_modifications": group.has_modifications,
            "leaf": self._model_to_dict(group.leaf) if group.leaf else None,
            "modifications": [self._model_to_dict(m) for m in group.modifications],
        }

    def _model_to_dict(self, model: ModelInfo) -> dict:
        key = str(model.dir)
        self._models_by_key[key] = model
        return {
            "key": key,
            "brand": model.brand,
            "name": model.name,
            "modification": model.modification,
            "display_label": model.display_label,
            "no_instruction": model.no_instruction,
            "has_wizard_spec": (model.dir / "_wizard_spec.json").exists(),
        }

    def _brand_logo_data_uri(self, brand: str) -> str | None:
        for filename in BRAND_LOGO_FILENAMES:
            path = self.cars_dir / brand / filename
            if path.exists():
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                mime = _LOGO_MIME.get(path.suffix.lower(), "image/png")
                return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        return None

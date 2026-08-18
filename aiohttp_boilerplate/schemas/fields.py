import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from marshmallow import Schema, fields, validate


class JoinNested(fields.Nested):

    def __init__(self, **kwargs: Any) -> None:
        self.table = kwargs.pop("table", None)
        self.joinOn = kwargs.pop("joinOn", None)
        self.joinType = kwargs.pop("joinType", "JOIN")

        if not self.table or not self.joinOn:
            raise ValueError("JoinNested requires both table and joinOn")
        super().__init__(**kwargs)


class Choice(fields.Raw):
    type = "choice"

    def get_validation(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "oneOf": self.choices,
        }

    def __init__(self, choices: Sequence[Any], **kwargs: Any) -> None:
        self.choices = choices
        v = kwargs.pop("validate", [])

        if isinstance(choices, list):

            def value(item: Any, key: str) -> Any:
                return item.get(key, item) if isinstance(item, dict) else item

            v.append(
                validate.OneOf(
                    [value(item, "value") for item in choices],
                    [value(item, "name") for item in choices],
                )
            )
        else:
            v.append(
                validate.OneOf(
                    [x for x in choices],
                )
            )
        kwargs["validate"] = v
        super().__init__(**kwargs)


class ChoiceConst(Choice):
    """Create choice field from json const array"""

    type = "choice"

    def get_validation(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "oneOf": self.const_file,
        }

    def __init__(self, const_file: str, const_folder: str = "const", **kwargs: Any) -> None:
        if "." in const_file:
            file_name, const_name = const_file.rsplit(".", 1)
        else:
            file_name, const_name = const_file, None
        file_name = file_name.replace(".", "/")
        file_path = const_folder + "/" + file_name + ".json"
        data = json.loads(Path(file_path).read_text(encoding="utf-8"))
        choices = data.get(const_name, data)
        self.const_file = const_file
        super().__init__(choices, **kwargs)


class FileRow(Schema):
    id = fields.Integer()
    url = fields.String()
    name = fields.Str()
    mime = fields.Str()
    bucket_id = fields.Int()
    meta_data = fields.Dict()


class FolderRow(Schema):
    files = fields.Nested(FileRow, required=True, many=True)
    meta_data = fields.Dict()


class FilerFile(fields.Integer):
    SIZE_10_MB = 10 * 1024 * 1024
    READBLE_FILE = [
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/vnd.oasis.opendocument.text",
    ]

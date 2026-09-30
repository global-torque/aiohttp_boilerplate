import json
import warnings
from collections.abc import Mapping
from contextlib import suppress
from itertools import count
from typing import Any, cast

import marshmallow
from aiohttp import hdrs, web
from aiohttp_cors import APP_CONFIG_KEY as CORS_CONFIG_KEY
from aiohttp_cors import CorsViewMixin
from marshmallow import fields as marshmallow_fields
from marshmallow_jsonschema import JSONSchema

from aiohttp_boilerplate.transactions import RequestConnectionMixin

from . import fixed_dump
from .exceptions import JSONHTTPError


# Schema is telling on how to transfer data from SQL to JSON format
class OptionsViewMixin(RequestConnectionMixin, CorsViewMixin):
    """Base class have implementation of the 'OPTIONS' method
    Class provide isamorphic way to do validation for front/backed
    """

    schema: type[marshmallow.Schema] | None = None

    request: web.Request
    request_data: Any

    # On start will always run before any other methods
    async def on_start(self) -> None:
        pass

    def _fields(self, schema: marshmallow.Schema) -> dict[str, Any]:
        return {}

    # Read data from request and save in request_data
    async def get_request_data(self, to_json: bool = False) -> Any:
        cast(Any, self.request).log.debug(
            f"Read data from request and save in request_data to_json={to_json}"
        )
        if self.request_data is None:
            self.request_data = await self.request.text()

        if to_json is True:
            if self.request.content_type != "application/json":
                raise JSONHTTPError(
                    self.request,
                    {"content_type": ["Expected application/json"]},
                    web.HTTPUnsupportedMediaType,
                )
            try:
                self.request_data = json.loads(self.request_data)
            except json.JSONDecodeError as err:
                raise JSONHTTPError(
                    self.request,
                    {"json": ["Malformed JSON body"]},
                    web.HTTPBadRequest,
                ) from err

        return self.request_data

    async def _options(self) -> dict[str, Any]:
        return (
            self.json_schema(self.schema())
            if hasattr(self, "schema") and self.schema is not None
            else {}
        )

    @classmethod
    def get_request_config(cls, request: web.Request, request_method: str) -> Mapping[str, Any]:
        try:
            return cast(
                Mapping[str, Any], cast(Any, super()).get_request_config(request, request_method)
            )
        except KeyError:
            # Keep aiohttp's 405 response for unsupported class-view methods.
            # Preflights must still reject the unsupported requested method.
            if request.method == hdrs.METH_OPTIONS:
                raise
            return {}

    # Will return options request with fields meta data
    async def options(self) -> web.Response:
        cors_enabled = (
            self.request.method == hdrs.METH_OPTIONS and CORS_CONFIG_KEY in self.request.app
        )
        preflight = None
        if cors_enabled and hdrs.ACCESS_CONTROL_REQUEST_METHOD in self.request.headers:
            preflight = await cast(Any, CorsViewMixin).options(self)

        response = self.json_response(await self._options())
        if preflight is not None:
            response.headers.update(preflight.headers)
        elif cors_enabled and (origin := self.request.headers.get(hdrs.ORIGIN)):
            # aiohttp-cors leaves every OPTIONS response to class-based views,
            # including schema requests that are not browser preflights.
            config = self.get_request_config(self.request, hdrs.METH_OPTIONS)
            options = config.get(origin, config.get("*"))
            if options is not None:
                response.headers[hdrs.ACCESS_CONTROL_ALLOW_ORIGIN] = origin
                if options.allow_credentials:
                    response.headers[hdrs.ACCESS_CONTROL_ALLOW_CREDENTIALS] = "true"
                if options.expose_headers:
                    exposed = (
                        response.headers
                        if options.expose_headers == "*"
                        else options.expose_headers
                    )
                    response.headers[hdrs.ACCESS_CONTROL_EXPOSE_HEADERS] = ",".join(exposed)
        return response

    @staticmethod
    def json_response(data: Any, status: int = 200) -> web.Response:
        return web.json_response(
            data,
            dumps=fixed_dump,
            status=status,
        )

    def json_schema(self, schema: marshmallow.Schema) -> dict[str, Any]:
        return JSONSchema().dump(schema)


class OptionsView(OptionsViewMixin, web.View):
    """Compatible initialized schema/custom-handler view base."""

    def __init__(self, request: web.Request) -> None:
        cast(Any, request).log.debug("Init OptionsView")
        super().__init__(request)
        self.request_data: Any = None
        self.app = self.request.app
        from aiohttp_boilerplate.transactions import application_pool

        self.db_pool = application_pool(self.request.app)


# Options request with a schema data
class SchemaOptionsView(OptionsView):
    obj: Any

    def __init__(self, request: web.Request) -> None:
        super().__init__(request)
        self.schema = self.get_schema()

    def get_schema(self) -> type[marshmallow.Schema] | None:
        warnings.warn(
            "Redefine get_schema in inherited class",
            RuntimeWarning,
            stacklevel=2,
        )
        return None

    async def get_schema_data(
        self,
        partial: bool = False,
        schema: type[marshmallow.Schema] | None = None,
    ) -> dict[str, Any]:
        if schema is None:
            schema = self.schema

        cast(Any, self.request).log.debug("load schema input")

        if self.request.content_type != "application/json":
            raise JSONHTTPError(
                self.request,
                {"content_type": ["Expected application/json"]},
                web.HTTPUnsupportedMediaType,
            )
        data = await self.get_request_data()
        if not data:
            raise JSONHTTPError(self.request, {"__error__": ["Empty data"]})

        cast(Any, self.request).log.debug("validate schema input", extra={"partial": partial})
        if schema is None:
            raise RuntimeError("SchemaOptionsView requires a schema")
        try:
            schema_result = schema().loads(data, partial=partial)
        except marshmallow.ValidationError as err:
            raise JSONHTTPError(self.request, err.messages) from err
        except (json.JSONDecodeError, TypeError, ValueError) as err:
            raise JSONHTTPError(self.request, {"json": ["Malformed JSON body"]}) from err

        if not isinstance(schema_result, dict):
            raise RuntimeError("schema load must return a mapping")
        return schema_result

    # Return json schema for marshmellow form)
    def json_schema(self, schema: marshmallow.Schema) -> dict[str, Any]:
        json_schema = JSONSchema()
        return json_schema.dump(schema)

    # Will return options request with validation data for a frontend
    def _getValidation(self, field: Any) -> dict[str, Any]:
        rules: dict[str, Any] = {}

        if getattr(field, "get_validation", None):
            return cast(dict[str, Any], field.get_validation())

        if field.validate:
            for v in field.validate:
                rules_name = v.__class__.__name__
                if rules_name == "OneOf":
                    rules["oneOf"] = "choices"
                    rules["choices"] = {}

                    for i, val in enumerate(v.choices):
                        try:
                            rules["choices"][val] = v.labels[i]
                        except IndexError:
                            rules["choices"][val] = val

                elif rules_name == "Length":
                    if v.min:
                        rules["minLength"] = v.min
                    if v.max:
                        rules["maxLength"] = v.max

                elif rules_name == "Range":
                    if v.min and v.max:
                        rules["range"] = [v.min, v.max]
                    if v.min:
                        rules["min"] = v.min
                    if v.max:
                        rules["max"] = v.max
                else:
                    rules[rules_name.lower()] = rules_name

        if field.required:
            rules["required"] = field.required

        return rules

    # Return fields information and validation data
    def _fields(self, schema: marshmallow.Schema) -> dict[str, Any]:

        return {
            name: (
                {
                    "type": field.__class__.__name__.lower(),
                    "many": cast(Any, field).many,
                    "schema": self._fields(cast(Any, field).schema),
                }
                if field.__class__.__name__.lower() == "nested"
                else {
                    "type": field.__class__.__name__.lower(),
                    "validate": self._getValidation(field),
                }
            )
            for name, field in schema.fields.items()
        }

    # Check if schema have NestedJoin Fields
    def schema_have_joins(self) -> bool:
        if callable(self.schema):
            schema = self.schema()
            for field in schema.fields:
                if schema.fields[field].__class__.__name__ == "JoinNested":
                    return True
        return False

    def add_fields_from_schema(
        self,
        _schema: marshmallow.Schema,
        _index: str = "t0",
        t_index: int = 1,
        parent_schema: str = "",
    ) -> dict[str, Any]:
        """Build SELECT fields and joins with one monotonic alias allocator."""
        aliases: dict[str, str] = {"t0": ""}
        selected: list[str] = []
        joins: list[str] = []
        allocator = count(t_index)

        def visit(schema: marshmallow.Schema, table_alias: str, output_path: str) -> None:
            for name, field in schema.fields.items():
                if field.load_only:
                    continue
                db_field = field.metadata.get("db_field", None)
                if db_field is False:
                    continue
                if field.__class__.__name__ == "JoinNested":
                    join_field = cast(Any, field)
                    if not join_field.table or not join_field.joinOn:
                        raise ValueError(f"JoinNested {name} requires table and joinOn")
                    alias = f"t{next(allocator)}"
                    aliases[alias] = f"{output_path}.{name}" if output_path else name
                    joins.append(
                        f"{join_field.joinType} {join_field.table} as {alias} "
                        f"on {alias}.{join_field.joinOn}"
                    )
                    visit(join_field.nested(), alias, aliases[alias])
                    continue
                if isinstance(
                    field,
                    (
                        marshmallow_fields.Method,
                        marshmallow_fields.Function,
                        marshmallow_fields.Constant,
                    ),
                ):
                    continue
                source_name = field.attribute or name
                expression = (
                    db_field.format(t_index=table_alias)
                    if isinstance(db_field, str) and db_field
                    else f"{table_alias}.{source_name}"
                )
                selected.append(f"{expression} as {table_alias}__{name}")

        visit(_schema, _index, parent_schema)
        return {
            "fields": ",".join(selected),
            "aliases": aliases,
            "sql_tables": (" " + " ".join(joins) + " " if joins else ""),
        }

    # Helper to convert data into beautifull json
    def join_prepare_fields(self, fields: str = "*") -> tuple[dict[str, str], str]:
        _fields: list[str] = []

        alias: dict[str, str] = {"t0": ""}

        sql = self.obj.sql
        if hasattr(self, "objects"):
            sql = self.objects.sql

        sql.table = self.obj.table

        if self.schema:
            schema_data = self.add_fields_from_schema(self.schema())
            _fields.append(schema_data["fields"])
            alias.update(schema_data["aliases"])
            sql.table += schema_data["sql_tables"]

        fields = ",".join(_fields) if len(_fields) else fields
        return alias, fields

    # Make beautiful json output
    def join_beautiful_output(
        self, aliases: Mapping[str, str], raw_data: Mapping[str, Any] | None
    ) -> dict[str, Any]:

        if raw_data is None:
            return {}

        temp: dict[str, Any] = {}
        for k, v in raw_data.items():
            d = k.split("__")
            if len(d) == 1:
                temp[k] = v
            elif aliases[d[0]] == "":
                temp[d[1]] = v
            else:
                t = temp
                for key in aliases[d[0]].split("."):
                    if key not in t:
                        t[key] = {}
                    t = t[key]
                t[d[1]] = v

        return temp


# Options request for a signle object
class ObjectView(SchemaOptionsView):
    """Base class have implementation to work with Single Object
    schema will be use to save or retrive data from database
    context will be to keep context of get requests
    """

    obj: Any

    def __init__(self, request: web.Request) -> None:
        super().__init__(request)

        self.id = None
        model = self.get_model()
        if model is None:
            warnings.warn("get_model return None", RuntimeWarning, stacklevel=2)
        else:
            self.obj = model(
                db_pool=self.db_pool,
                log=cast(Any, request).log,
            )

    # Return model object
    def get_model(self) -> type[Any] | None:
        warnings.warn("Redefine get_model in inherited class", RuntimeWarning, stacklevel=2)
        return None

    # Return object id from request
    async def get_id(self) -> str | int:
        object_id: str | int | None = self.request.match_info.get("id")

        if object_id is None:
            raise JSONHTTPError(self.request, {"__error__": ["No id found"]})
        # ToDo
        # Check if aiohttp can parse string/numeric data
        with suppress(ValueError):
            object_id = int(object_id)

        return object_id

    # Return context for and object
    async def get_data(self, obj: Any) -> dict[str, Any] | list[Any]:
        cast(Any, self.request).log.debug("serialize object response")

        if not self.schema:
            return cast(dict[str, Any] | list[Any], self.obj.data)

        schema = self.schema()
        data: dict[str, Any] = {}
        for name, field in schema.fields.items():
            if field.load_only:
                continue
            source_name = field.attribute or name
            if isinstance(getattr(obj, "data", None), dict) and source_name in obj.data:
                data[source_name] = obj.data[source_name]
            elif hasattr(obj, source_name):
                data[source_name] = getattr(obj, source_name)

        return cast(dict[str, Any] | list[Any], schema.dump(data))

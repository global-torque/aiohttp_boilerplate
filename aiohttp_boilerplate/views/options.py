import json
import warnings
import marshmallow

from aiohttp import web

from . import fixed_dump
from .exceptions import JSONHTTPError
from marshmallow_jsonschema import JSONSchema


# Schema is telling on how to transfer data from SQL to JSON format
class OptionsView(web.View):
    """ Base class have implementation of the 'OPTIONS' method
        Class provide isamorphic way to do validation for front/backed
    """

    def __init__(self, request):
        request.log.debug("Init OptionsView")
        super().__init__(request)
        self.request_data = None
        self.app = self.request.app
        self.db_pool = self.request.app.db_pool

    # On start will always run before any other methods
    async def on_start(self):
        pass

    async def _fields(self, schema):
        return {}

    # Read data from request and save in request_data
    async def get_request_data(self, to_json=False):
        self.request.log.debug(f"Read data from request and save in request_data to_json={to_json}")
        if self.request_data is None:
            self.request_data = await self.request.text()

        if to_json is True:
            self.request_data = json.loads(self.request_data)

        return self.request_data

    async def _options(self):
        return self.json_schema(self.schema()) if hasattr(self, 'schema') \
            and self.schema is not None else {}

    # Will return options request with fields meta data
    async def options(self):
        return self.json_response(await self._options())

    @staticmethod
    def json_response(data, status=200):
        return web.json_response(
            data,
            dumps=fixed_dump,
            status=status,
        )


# Options request with a schema data
class SchemaOptionsView(OptionsView):

    def __init__(self, request):
        super().__init__(request)
        self.schema = self.get_schema()

    def get_schema(self):
        self.request.log.warn('Redefine get_schema in inherited class', RuntimeWarning)
        return None

    async def get_schema_data(self, partial=False, schema=None):
        if schema is None:
            schema = self.schema

        self.request.log.debug(f"schema={schema}")

        data = await self.get_request_data()
        if not data:
            raise JSONHTTPError(self.request, {'__error__': ['Empty data']})

        self.request.log.debug(f"validate data=${data}, partial=${partial}")
        try:
            schema_result = schema().loads(data, partial=partial)
        except marshmallow.ValidationError as err:
            raise JSONHTTPError(self.request, err.messages)
        except Exception as err:
            raise JSONHTTPError(self.request, err)

        return schema_result

     # Return json schema for marshmellow form)
    def json_schema(self, schema):
        json_schema = JSONSchema()
        return json_schema.dump(schema)

    # Will return options request with validation data for a frontend
    def _getValidation(self, field):
        rules = {}

        if getattr(field, 'get_validation', None):
            return field.get_validation()

        if field.validate:
            for v in field.validate:
                rules_name = v.__class__.__name__
                if rules_name == 'OneOf':
                    rules['oneOf'] = 'choices'
                    rules['choices'] = {}

                    for i, val in enumerate(v.choices):
                        try:
                            rules['choices'][val] = v.labels[i]
                        except IndexError:
                            rules['choices'][val] = val

                elif rules_name == 'Length':
                    if v.min:
                        rules['minLength'] = v.min
                    if v.max:
                        rules['maxLength'] = v.max

                elif rules_name == 'Range':
                    if v.min and v.max:
                        rules['range'] = [v.min, v.max]
                    if v.min:
                        rules['min'] = v.min
                    if v.max:
                        rules['max'] = v.max
                else:
                    rules[rules_name.lower()] = rules_name

        if field.required:
            rules['required'] = field.required

        return rules

    # Return fields information and validation data
    def _fields(self, schema):

        return {
            name: {
                'type': field.__class__.__name__.lower(),
                'many': field.many,
                'schema': self._fields(field.schema),
            } if field.__class__.__name__.lower() == 'nested'
            else {
                'type': field.__class__.__name__.lower(),
                'validate': self._getValidation(field),
            } for name, field in schema.fields.items()}

    # Check if schema have NestedJoin Fields
    def schema_have_joins(self):
        if callable(self.schema):
            schema = self.schema()
            for field in schema.fields:
                if schema.fields[field].__class__.__name__ == 'JoinNested':
                    return True
        return False

    def add_fields_from_schema(self, _schema, _index="t0", t_index=1, parent_schema=""):
        _fields = []
        aliases = {'t0': ''}
        sql_tables = ''

        for name, field in sorted(_schema.fields.items()):
            db_field = field.metadata.get('db_field', ''). \
                            format(t_index=_index) or f"{_index}.{name}"

            if field.__class__.__name__ == 'JoinNested':
                sql_tables += "{} {} as t{} on {}.{} ".format(
                    field.joinType,
                    field.table,
                    t_index,
                    't%d' % t_index,
                    field.joinOn
                )
                if parent_schema != "":
                    aliases['t{}'.format(t_index)] = "{}.{}".format(parent_schema, name)
                else:
                    aliases['t{}'.format(t_index)] = name
                nested_data = self.add_fields_from_schema(field.nested(), 't%d' % t_index, t_index + 1, aliases["t{}".format(t_index)])
                _fields.append(nested_data["fields"])
                aliases.update(nested_data["aliases"])
                sql_tables += nested_data["sql_tables"]
                t_index += 1
            else:
                if not field.dump_only:
                    _fields.append(f"{db_field} as {_index}__{name}")
        return {
            "fields": ",".join(_fields),
            "aliases": aliases,
            "sql_tables": sql_tables,
        }

    # Helper to convert data into beautifull json
    def join_prepare_fields(self, fields="*"):
        _fields = []

        alias = {'t0': ''}

        sql = self.obj.sql
        if hasattr(self, 'objects'):
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
    def join_beautiful_output(self, aliases, raw_data):

        if raw_data is None:
            return {}

        temp = {}
        for k, v in raw_data.items():
            d = k.split('__')
            if len(d) == 1:
                temp[k] = v
            elif aliases[d[0]] == '':
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
    """ Base class have implementation to work with Single Object
        schema will be use to save or retrive data from database
        context will be to keep context of get requests
    """

    def __init__(self, request):
        super().__init__(request)

        self.id = None
        if self.get_model() is None:
            warnings.warn('get_model return None', RuntimeWarning)
        else:
            self.obj = self.get_model()(db_pool=request.app.db_pool, log=request.log)

    # Return model object
    def get_model(self):
        warnings.warn('Redefine get_model in inherited class', RuntimeWarning)
        return None

    # Return object id from request
    async def get_id(self):
        id = self.request.match_info.get('id')

        if id is None:
            raise JSONHTTPError(self.request, {"__error__": ["No id found"]})
        # ToDo
        # Check if aiohttp can parse string/numeric data
        try:
            id = int(id)
        except ValueError:
            pass

        return id

    # Return context for and object
    async def get_data(self, obj):
        self.request.log.debug("Start get_data method for ObjectView", str(obj.data))

        if not self.schema:
            return self.obj.data

        schema = self.schema()
        data = {}
        for name, field in schema.fields.items():
            if field.load_only:
                continue
            if name == "data":
                data[name] = getattr(obj, name)["data"]
            else:
                data[name] = getattr(obj, name)

        return schema.dump(data)

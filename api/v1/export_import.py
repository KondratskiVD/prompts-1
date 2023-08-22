import json
from io import BytesIO
from itertools import chain

from flask import request, send_file
from pylon.core.tools import log

from sqlalchemy.exc import IntegrityError
from ...models.example import Example
from ...models.pd.example import ExampleModel
from ...models.pd.export_import import PromptExport, PromptImport
from ...models.pd.variable import VariableModel
from ...models.prompts import Prompt
from ...models.variable import Variable
from ...utils.ai_providers import AIProvider

from tools import api_tools, db


class ProjectAPI(api_tools.APIModeHandler):
    def get(self, project_id: int, prompt_id: int, **kwargs):
        with db.with_project_schema_session(project_id) as session:
            prompt = session.query(Prompt).filter(Prompt.id == prompt_id).one_or_none()
            
            if not prompt:
                return {'error': f'Prompt with id: {prompt_id} not found'}, 400

            prompt.project_id = project_id
            result = PromptExport.from_orm(prompt).dict_flat(
                exclude_unset=True, by_alias=False, exclude={'integration_id'}
            )            
            examples = session.query(Example).filter(
                Example.prompt_id == prompt_id,
            ).all()
            
            result['examples'] = [
                ExampleModel.from_orm(i).dict(exclude={'id', 'prompt_id'})
                for i in examples
            ]
            variables = session.query(Variable).filter(
                Variable.prompt_id == prompt_id,
            ).all()
            result['variables'] = [
                VariableModel.from_orm(i).dict(exclude={'id', 'prompt_id'})
                for i in variables
            ]
            if 'as_file' in request.args:
                file = BytesIO()
                data = json.dumps(result, ensure_ascii=False, indent=4)
                file.write(data.encode('utf-8'))
                file.seek(0)
                return send_file(file, download_name=f'{prompt.name}.json', as_attachment=False)
            return result, 200


    def post(self, project_id: int, **kwargs):
        try:
            integration_id = request.json['integration_id']
            if not integration_id:
                raise ValueError
        except (KeyError, ValueError):
            return {'error': '"integration_id" is required'}, 400

        examples = request.json.pop('examples', [])
        variables = request.json.pop('variables', [])
        try:
            prompt_data = PromptImport.parse_obj(request.json)
        except Exception as e:
            log.critical(str(e))
            return {'error': str(e)}, 400

        prompt_dict = prompt_data.dict(exclude_unset=False, by_alias=True)
        log.info('settings parse result: %s', prompt_dict['model_settings'])
        
        if request.json.get('skip'):
            return {
                'examples': examples,
                'variables': variables,
                **prompt_dict
            }, 200

        try:
            p = self.module.create(project_id=project_id, prompt=prompt_dict)
        except IntegrityError:
            return {'error': f'Prompt name \'{prompt_dict["name"]}\' already exists'}, 400

        for i in chain(examples, variables):
            i['prompt_id'] = p['id']
        self.module.create_examples_bulk(project_id=project_id, examples=examples)
        self.module.create_variables_bulk(project_id=project_id, variables=variables)
        return self.module.get_by_id(project_id, p['id']), 201


# class AdminAPI(api_tools.APIModeHandler):
#     ...


class API(api_tools.APIBase):
    url_params = [
        '<int:project_id>/<int:prompt_id>',
        '<string:mode>/<int:project_id>/<int:prompt_id>',
        '<int:project_id>',
        '<string:mode>/<int:project_id>',
    ]

    mode_handlers = {
        'default': ProjectAPI,
        # 'administration': AdminAPI,
    }

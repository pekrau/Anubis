"Call for submissions."

import copy

import flask

from . import constants
from . import utils


blueprint = flask.Blueprint('call', __name__)

@blueprint.route('/', methods=['GET', 'POST'])
@utils.admin_required
def create():
    "Create a new call from scratch."
    if utils.http_GET():
        return flask.render_template('call/create.html')

    elif utils.http_POST():
        try:
            with CallSaver() as saver:
                saver.set_identifier(flask.request.form.get('identifier'))
                saver.set_title(flask.request.form.get('title'))
            call = saver.doc
        except ValueError as error:
            utils.flash_error(str(error))
            return flask.redirect(flask.url_for('.create'))
        return flask.redirect(flask.url_for('.edit', cid=call['identifier']))

@blueprint.route('/<cid>')
def display(cid):
    "Display the call."
    call = get_call(cid)
    if not call:
        utils.flash_error('no such call')
        return flask.redirect(flask.url_for('home'))
    return flask.render_template('call/display.html',
                                 call=call,
                                 is_editable=is_editable(call))

@blueprint.route('/<cid>/edit', methods=['GET', 'POST', 'DELETE'])
@utils.admin_required
def edit(cid):
    "Edit the call, or delete it."
    call = get_call(cid)
    if not call:
        utils.flash_error('no such call')
        return flask.redirect(flask.url_for('home'))

    if utils.http_GET():
        return flask.render_template('call/edit.html', call=call)

    elif utils.http_POST():
        try:
            with CallSaver(call) as saver:
                saver.set_title(flask.request.form.get('title'))
                saver['description'] = flask.request.form.get('description')
                saver['opens'] = utils.normalize_datetime(
                    flask.request.form.get('opens'))
                saver['closes'] = utils.normalize_datetime(
                    flask.request.form.get('closes'))
        except ValueError as error:
            utils.flash_error(str(error))
        return flask.redirect(flask.url_for('.display', cid=call['identifier']))

    elif utils.http_DELETE():
        if not is_editable(call):
            utils.flash_error('call cannot be deleted')
            return flask.redirect(
                flask.url_for('.display', cid=call['identifier']))
        utils.delete(call)
        utils.flash_message(f"deleted call {call['title']}")
        return flask.redirect(flask.url_for('calls.all'))


@blueprint.route('/<cid>/field', methods=['POST'])
@utils.admin_required
def add_field(cid):
    "Add an input field to the call."
    call = get_call(cid)
    if not call:
        utils.flash_error('no such call')
        return flask.redirect(flask.url_for('home'))

    if utils.http_POST():
        try:
            with CallSaver(call) as saver:
                saver.add_field(form=flask.request.form)
        except ValueError as error:
            utils.flash_error(str(error))
            return flask.redirect(
                flask.url_for('.add_field', cid=call['identifier']))
        return flask.redirect(flask.url_for('.display', cid=call['identifier']))

@blueprint.route('/<cid>/field/<fid>', methods=['POST', 'DELETE'])
@utils.admin_required
def edit_field(cid, fid):
    "Edit the input field of the call."
    call = get_call(cid)
    if not call:
        utils.flash_error('no such call')
        return flask.redirect(flask.url_for('home'))

    if utils.http_POST():
        try:
            with CallSaver(call) as saver:
                saver.edit_field(fid, form=flask.request.form)
        except (KeyError, ValueError) as error:
            utils.flash_error(str(error))
        return flask.redirect(flask.url_for('.display', cid=call['identifier']))

    elif utils.http_DELETE():
        with CallSaver(call) as saver:
            saver.delete_field(fid)
        return flask.redirect(flask.url_for('.display', cid=call['identifier']))

@blueprint.route('/<cid>/clone', methods=['GET', 'POST'])
@utils.admin_required
def clone(cid):
    "Clone the call."
    call = get_call(cid)
    if not call:
        utils.flash_error('no such call')
        return flask.redirect(flask.url_for('home'))

    if utils.http_GET():
        return flask.render_template('call/clone.html', call=call)

    elif utils.http_POST():
        try:
            with CallSaver() as saver:
                saver.set_identifier(flask.request.form.get('identifier'))
                saver.set_title(flask.request.form.get('title'))
                saver.doc['fields'] = copy.deepcopy(call['fields'])
            new = saver.doc
        except ValueError as error:
            utils.flash_error(str(error))
            return flask.redirect(flask.url_for('.clone', cid=cid))
        return flask.redirect(flask.url_for('.edit', cid=new['identifier']))

@blueprint.route('/<cid>/logs')
@utils.admin_required
def logs(cid):
    "Display the log records of the call."
    call = get_call(cid)
    if call is None:
        utils.flash_error('no such call')
        return flask.redirect(flask.url_for('home'))

    return flask.render_template(
        'logs.html',
        title=f"Call {call['identifier']}",
        cancel_url=flask.url_for('.display', cid=call['identifier']),
        logs=utils.get_logs(call['_id']))

@blueprint.route('/<cid>/submission', methods=['POST'])
@utils.login_required
def submission(cid):
    "Create a new submission within the call."
    import anubis.submission 
    call = get_call(cid)
    if call is None:
        utils.flash_error('no such call')
        return flask.redirect(flask.url_for('home'))

    if not call['tmp']['is_open']:
        utils.flash_error(f"Call {call['title']} is not open.")

    if utils.http_POST():
        with anubis.submission.SubmissionSaver() as saver:
            saver.set_call(call)
        doc = saver
        return flask.redirect(
            flask.url_for('submission.display', sid=doc['identifier']))


class CallSaver(utils.BaseSaver):
    "Call document saver context."

    DOCTYPE = constants.DOCTYPE_CALL

    def initialize(self):
        self.doc['opens'] = None
        self.doc['closes'] = None
        self.doc['fields'] = []

    def set_identifier(self, identifier):
        "Call identifier."
        if self.doc.get('identifier'):
            raise ValueError('identifier has already been set')
        if not identifier:
            raise ValueError('identifier must be provided')
        if len(identifier) > flask.current_app.config['CALL_IDENTIFIER_MAXLENGTH']:
            raise ValueError('too long identifier')
        if not constants.ID_RX.match(identifier):
            raise ValueError('invalid identifier')
        if get_call(identifier):
            raise ValueError('identifier is already in use')
        self.doc['identifier'] = identifier

    def set_title(self, title):
        "Call title: non-blank required."
        if not title:
            raise ValueError('title must be provided')
        self.doc['title'] = title

    def add_field(self, form=dict()):
        field = {'type': form.get('type'),
                 'identifier': form.get('identifier'),
                 'title': form.get('title') or None,
                 'description': form.get('description') or None,
                 'required': bool(form.get('required'))
                 }
        if not (field['identifier'] and 
                constants.ID_RX.match(field['identifier'])):
            raise ValueError('invalid identifier')
        if field['type'] == 'text':
            pass
        else:
            raise ValueError('invalid field type')
        self.doc['fields'].append(field)

    def edit_field(self, fid, form=dict()):
        for field in self.doc['fields']:
            if field['identifier'] == fid: break
        else:
            raise KeyError('no such field')
        field['title'] = form.get('title') or None
        field['description'] = form.get('description') or None
        field['required'] = bool(form.get('required'))
        # XXX according to field type...

    def delete_field(self, fid):
        for pos, field in enumerate(self.doc['fields']):
            if field['identifier'] == fid:
                self.doc['fields'].pop(pos)
                break
        else:
            raise ValueError('no such field')


def get_call(cid):
    "Return the call with the given identifier."
    result = [r.doc for r in flask.g.db.view('calls', 'identifier',
                                             key=cid,
                                             include_docs=True)]
    if len(result) == 1:
        call = result[0]
        set_call_tmp(call)
        return call
    else:
        return None

def is_editable(call):
    "Is the given call editable? Check open/closed and privileges."
    if call['tmp']['submissions_count'] != 0: return False
    if flask.g.is_admin: return True
    if call['tmp']['is_open']: return False
    if call['tmp']['is_closed']: return False
    return False

def set_call_tmp(call):
    """Set the 'tmp' property of the call. This is computed data that
    will not be stored with the document: is_open, is_closed, 
    display data, submissions count.
    """
    call['tmp'] = {}
    result = list(flask.g.db.view('submissions', 'call',
                                  key=call['identifier'],
                                  reduce=True))
    if result:
        call['tmp']['submissions_count'] = result[0].value
    else:
        call['tmp']['submissions_count'] = 0
    now = utils.normalized_local_now()
    if call['opens']:
        if call['opens'] > now:
            call['tmp']['is_open'] = False
            call['tmp']['is_closed'] = False
            call['tmp']['text'] = 'Not yet open.'
            call['tmp']['color'] = 'secondary'
        elif call['closes']:
            remaining = utils.days_remaining(call['closes'])
            if remaining > 7.0:
                call['tmp']['is_open'] = True
                call['tmp']['is_closed'] = False
                call['tmp']['text'] = f"{remaining:.0f} days remaining."
                call['tmp']['color'] = 'success'
            elif remaining > 2.0:
                call['tmp']['is_open'] = True
                call['tmp']['is_closed'] = False
                call['tmp']['text'] = f"{remaining:.0f} days remaining."
                call['tmp']['color'] = 'info'
            elif remaining >= 1.0:
                call['tmp']['is_open'] = True
                call['tmp']['is_closed'] = False
                call['tmp']['text'] = "Less than two days remaining."
                call['tmp']['color'] = 'warning'
            elif remaining >= 0.0:
                call['tmp']['is_open'] = True
                call['tmp']['is_closed'] = False
                call['tmp']['text'] = "Less than one day remaining."
                call['tmp']['color'] = 'danger'
            else:
                call['tmp']['is_open'] = False
                call['tmp']['is_closed'] = True
                call['tmp']['text'] = 'Closed.'
                call['tmp']['color'] = 'dark'
        else:
            call['tmp']['is_open'] = True
            call['tmp']['is_closed'] = False
            call['tmp']['text'] = 'Open with no closing date.'
            call['tmp']['color'] = 'success'
    else:
        if call['closes']:
            call['tmp']['is_open'] = False
            call['tmp']['is_closed'] = False
            call['tmp']['text'] = 'No open date set.'
            call['tmp']['color'] = 'secondary'
        else:
            call['tmp']['is_open'] = False
            call['tmp']['is_closed'] = False
            call['tmp']['text'] = 'No open or close dates set.'
            call['tmp']['color'] = 'secondary'

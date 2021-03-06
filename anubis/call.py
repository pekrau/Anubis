"Call for proposals."

import copy
import io
import zipfile

import flask

import anubis.proposal
import anubis.proposals
import anubis.user
from anubis import constants
from anubis import utils
from anubis.saver import AttachmentSaver


def init(app):
    "Initialize; update CouchDB design documents."
    db = utils.get_db(app=app)
    if db.put_design('calls', DESIGN_DOC):
        app.logger.info('Updated calls design document.')

DESIGN_DOC = {
    'views': {
        'identifier': {'map': "function (doc) {if (doc.doctype !== 'call') return; emit(doc.identifier, null);}"},
        'closes': {'map': "function (doc) {if (doc.doctype !== 'call' || !doc.closes || !doc.opens) return; emit(doc.closes, doc.identifier);}"},
        'open_ended': {'map': "function (doc) {if (doc.doctype !== 'call' || !doc.opens || doc.closes) return; emit(doc.opens, doc.identifier);}"},
        'owner': {'reduce': '_count',
                  'map': "function (doc) {if (doc.doctype !== 'call') return; emit(doc.owner, doc.identifier);}"},
        'reviewer': {'reduce': '_count',
                     'map': "function (doc) {if (doc.doctype !== 'call') return; for (var i=0; i < doc.reviewers.length; i++) {emit(doc.reviewers[i], doc.identifier); }}"},
    }
}

blueprint = flask.Blueprint('call', __name__)

@blueprint.route('/', methods=['GET', 'POST'])
@utils.login_required
def create():
    "Create a new call from scratch."
    if not allow_create():
        return utils.error('You are not allowed to create a call.')

    if utils.http_GET():
        return flask.render_template('call/create.html')

    elif utils.http_POST():
        try:
            with CallSaver() as saver:
                saver.set_identifier(flask.request.form.get('identifier'))
                saver.set_title(flask.request.form.get('title'))
            call = saver.doc
        except ValueError as error:
            return utils.error(error)
        return flask.redirect(flask.url_for('.edit', cid=call['identifier']))

@blueprint.route('/<cid>')
def display(cid):
    "Display the call."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))
    if not allow_view(call):
        return utils.error('You are not allowed to view the call.')
    kwargs = {}
    if flask.g.current_user:
        kwargs['my_proposal'] = anubis.proposal.get_call_user_proposal(
            cid, flask.g.current_user['username'])
        kwargs['my_reviews_count'] = utils.get_count(
            'reviews', 'call_reviewer',
            [call['identifier'], flask.g.current_user['username']])
        kwargs['call_proposals_count'] = utils.get_call_proposals_count(cid)
        if call.get('categories'):
            kwargs['call_proposals_category_counts'] = dict(
                [(c, utils.get_call_proposals_count(cid, c))
                 for c in sorted(call.get('categories'))])
    return flask.render_template('call/display.html',
                                 call=call,
                                 am_call_owner=am_call_owner(call),
                                 am_reviewer=am_reviewer(call),
                                 allow_edit=allow_edit(call),
                                 allow_delete=allow_delete(call),
                                 allow_proposal=allow_proposal(call),
                                 allow_view_details=allow_view_details(call),
                                 allow_view_reviews=allow_view_reviews(call),
                                 **kwargs)

@blueprint.route('/<cid>/edit', methods=['GET', 'POST', 'DELETE'])
@utils.login_required
def edit(cid):
    "Edit the call, or delete it."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))
    if not allow_edit(call):
        return utils.error('You are not allowed to edit the call.')

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
                saver['reviews_due'] = utils.normalize_datetime(
                    flask.request.form.get('reviews_due'))
                saver.edit_access(flask.request.form)
                saver.set_categories(flask.request.form)
        except ValueError as error:
            utils.flash_error(error)
        return flask.redirect(flask.url_for('.display', cid=call['identifier']))

    elif utils.http_DELETE():
        if not allow_delete(call):
            return utils.error('You are not allowed to delete the call.')
        utils.delete(call)
        utils.flash_message(f"Deleted call {call['identifier']}:{call['title']}.")
        return flask.redirect(
            flask.url_for('calls.owner',
                          username=flask.g.current_user['username']))

@blueprint.route('/<cid>/documents', methods=['GET', 'POST'])
@utils.login_required
def documents(cid):
    "Display documents for delete, or add document (attachment file)."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))
    if not allow_edit(call):
        return utils.error('You are not allowed to edit the call.')

    if utils.http_GET():
        return flask.render_template('call/documents.html', call=call)

    elif utils.http_POST():
        infile = flask.request.files.get('document')
        if infile:
            description = flask.request.form.get('document_description')
            with CallSaver(call) as saver:
                saver.add_document(infile, description)
        else:
            utils.flash_error('No document selected.')
        return flask.redirect(flask.url_for('.display', cid=call['identifier']))

@blueprint.route('/<cid>/documents/<documentname>',
                 methods=['GET', 'POST', 'DELETE'])
def document(cid, documentname):
    "Download the given document (attachment file), or delete it."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))

    if utils.http_GET():
        if not (flask.g.am_admin or call['tmp']['is_published']):
            return utils.error(f"Call {call['title']} has not been published.")
        try:
            stub = call['_attachments'][documentname]
        except KeyError:
            return utils.error('No such document in call.')
        outfile = flask.g.db.get_attachment(call, documentname)
        response = flask.make_response(outfile.read())
        response.headers.set('Content-Type', stub['content_type'])
        response.headers.set('Content-Disposition', 'attachment', 
                             filename=documentname)
        return response

    elif utils.http_DELETE():
        if not flask.g.am_admin:
            return utils.error('You may not delete a document in the call.')
        with CallSaver(call) as saver:
            saver.delete_document(documentname)
        return flask.redirect(
            flask.url_for('.documents', cid=call['identifier']))

@blueprint.route('/<cid>/proposal', methods=['GET', 'POST'])
@utils.login_required
def proposal(cid):
    "Display proposal fields for delete, and add field."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))
    if not allow_edit(call):
        return utils.error('You are not allowed to edit the call.')

    if utils.http_GET():
        return flask.render_template('call/proposal.html', call=call)

    elif utils.http_POST():
        try:
            with CallSaver(call) as saver:
                saver.add_proposal_field(flask.request.form)
        except ValueError as error:
            utils.flash_error(error)
        return flask.redirect(flask.url_for('.proposal',cid=call['identifier']))

@blueprint.route('/<cid>/proposal/<fid>', methods=['POST', 'DELETE'])
@utils.login_required
def proposal_field(cid, fid):
    "Edit or delete the proposal field."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))
    if not allow_edit(call):
        return utils.error('You are not allowed to edit the call.')

    if utils.http_POST():
        try:
            with CallSaver(call) as saver:
                saver.edit_proposal_field(fid, flask.request.form)
        except (KeyError, ValueError) as error:
            utils.flash_error(error)
        return flask.redirect(flask.url_for('.proposal',cid=call['identifier']))

    elif utils.http_DELETE():
        try:
            with CallSaver(call) as saver:
                saver.delete_proposal_field(fid)
        except ValueError as error:
            utils.flash_error(error)
        return flask.redirect(flask.url_for('.proposal',cid=call['identifier']))

@blueprint.route('/<cid>/reviewers', methods=['GET', 'POST', 'DELETE'])
@utils.login_required
def reviewers(cid):
    "Edit the list of reviewers."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))
    if not allow_edit(call):
        return utils.error('You are not allowed to edit the call.')

    if utils.http_GET():
        return flask.render_template('call/reviewers.html', call=call)

    elif utils.http_POST():
        reviewer = flask.request.form.get('reviewer')
        if not reviewer:
            return flask.redirect(flask.url_for('.display', cid=cid))
        user = anubis.user.get_user(username=reviewer)
        if user is None:
            user = anubis.user.get_user(email=reviewer)
        if user is None:
            return utils.error('No such user.')
        if anubis.proposal.get_call_user_proposal(cid, user['username']):
            return utils.error('User has a proposal in the call.',
                               flask.url_for('.reviewers',
                                             cid=call['identifier']))

        if user['username'] not in call['reviewers']:
            with CallSaver(call) as saver:
                saver['reviewers'].append(user['username'])
                if utils.to_bool(flask.request.form.get('chair')):
                    saver['chairs'].append(user['username'])
        return flask.redirect(
            flask.url_for('.reviewers', cid=call['identifier']))

    elif utils.http_DELETE():
        reviewer = flask.request.form.get('reviewer')
        if utils.get_docs_view('reviews', 'call_reviewer',
                                  [call['identifier'], reviewer]):
            return utils.error('Cannot remove reviewer which has reviews'
                               ' in the call.')
        if reviewer:
            with CallSaver(call) as saver:
                try:
                    saver['reviewers'].remove(reviewer)
                except ValueError:
                    pass
                try:
                    saver['chairs'].remove(reviewer)
                except ValueError:
                    pass
        return flask.redirect(
            flask.url_for('.reviewers', cid=call['identifier']))

@blueprint.route('/<cid>/review', methods=['GET', 'POST'])
@utils.login_required
def review(cid):
    "Display review fields for delete, and add field."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))
    if not allow_edit(call):
        return utils.error('You are not allowed to edit the call.')

    if utils.http_GET():
        return flask.render_template('call/review.html', call=call)

    elif utils.http_POST():
        try:
            with CallSaver(call) as saver:
                saver.add_review_field(flask.request.form)
        except ValueError as error:
            utils.flash_error(error)
        return flask.redirect(flask.url_for('.review', cid=call['identifier']))

@blueprint.route('/<cid>/review/<fid>', methods=['POST', 'DELETE'])
@utils.login_required
def review_field(cid, fid):
    "Edit or delete the review field."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))
    if not allow_edit(call):
        return utils.error('You are not allowed to edit the call.')

    if utils.http_POST():
        try:
            with CallSaver(call) as saver:
                saver.edit_review_field(fid, flask.request.form)
        except ValueError as error:
            utils.flash_error(error)
        return flask.redirect(flask.url_for('.review', cid=call['identifier']))

    elif utils.http_DELETE():
        try:
            with CallSaver(call) as saver:
                saver.delete_review_field(fid)
        except ValueError as error:
            utils.flash_error(error)
        return flask.redirect(flask.url_for('.review', cid=call['identifier']))

@blueprint.route('/<cid>/decision', methods=['GET', 'POST'])
@utils.login_required
def decision(cid):
    "Display decision fields for delete, and add field."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))
    if not allow_edit(call):
        return utils.error('You are not allowed to edit the call.')

    if utils.http_GET():
        return flask.render_template('call/decision.html', call=call)

    elif utils.http_POST():
        try:
            with CallSaver(call) as saver:
                saver.add_decision_field(flask.request.form)
        except ValueError as error:
            utils.flash_error(error)
        return flask.redirect(flask.url_for('.decision',cid=call['identifier']))

@blueprint.route('/<cid>/decision/<fid>', methods=['POST', 'DELETE'])
@utils.login_required
def decision_field(cid, fid):
    "Edit or delete the decision field."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))
    if not allow_edit(call):
        return utils.error('You are not allowed to edit the call.')

    if utils.http_POST():
        try:
            with CallSaver(call) as saver:
                saver.edit_decision_field(fid, flask.request.form)
        except (KeyError, ValueError) as error:
            utils.flash_error(error)
        return flask.redirect(flask.url_for('.decision',cid=call['identifier']))

    elif utils.http_DELETE():
        try:
            with CallSaver(call) as saver:
                saver.delete_decision_field(fid)
        except ValueError as error:
            utils.flash_error(error)
        return flask.redirect(flask.url_for('.decision',cid=call['identifier']))

@blueprint.route('/<cid>/reset_counter', methods=['POST'])
@utils.login_required
def reset_counter(cid):
    "Reset the counter of the call. Only if no proposals in it."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))
    if not allow_edit(call):
        return utils.error('You are not allowed to edit the call.')
    if utils.get_call_proposals_count(cid) != 0:
        return utils.error('Cannot reset counter when there are proposals in the call.')

    with CallSaver(call) as saver:
        saver['counter'] = None
    utils.flash_message('Counter for proposals in call reset.')
    return flask.redirect(flask.url_for('.display', cid=call['identifier']))

@blueprint.route('/<cid>/clone', methods=['GET', 'POST'])
@utils.login_required
def clone(cid):
    "Clone the call."
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))

    if not allow_create():
        return utils.error('You are not allowed to create a call.')

    if utils.http_GET():
        return flask.render_template('call/clone.html', call=call)

    elif utils.http_POST():
        try:
            with CallSaver() as saver:
                saver.set_identifier(flask.request.form.get('identifier'))
                saver.set_title(flask.request.form.get('title'))
                saver.doc['proposal'] = copy.deepcopy(call['proposal'])
                saver.doc['review'] = copy.deepcopy(call['review'])
                saver.doc['decision'] = copy.deepcopy(call['decision'])
                # Do not copy documents.
                # Do not copy reviewers or chairs.
            new = saver.doc
        except ValueError as error:
            return utils.error(error)
        return flask.redirect(flask.url_for('.edit', cid=new['identifier']))

@blueprint.route('/<cid>/logs')
@utils.login_required
def logs(cid):
    "Display the log records of the call."
    call = get_call(cid)
    if call is None:
        return utils.error('No such call.', flask.url_for('home'))

    if not (flask.g.am_admin or am_call_owner(call)):
        return utils.error('You are not admin or owner of the call.')

    return flask.render_template(
        'logs.html',
        title=f"Call {call['identifier']}",
        back_url=flask.url_for('.display', cid=call['identifier']),
        logs=utils.get_logs(call['_id']))

@blueprint.route('/<cid>/create_proposal', methods=['POST'])
@utils.login_required
def create_proposal(cid):
    "Create a new proposal within the call. Redirect to an existing proposal."
    call = get_call(cid)
    if call is None:
        return utils.error('No such call.', flask.url_for('home'))
    if not call['tmp']['is_open']:
        return utils.error("The call is not open.")
    if not allow_proposal(call):
        return utils.error('You may not create a proposal in this call.',
                           flask.url_for('.display', cid=cid))

    if utils.http_POST():
        proposal = anubis.proposal.get_call_user_proposal(
            cid, flask.g.current_user['username'])
        if proposal:
            return utils.message('Proposal already exists for the call.',
                                 flask.url_for('proposal.display',
                                               pid=proposal['identifier']))
        else:
            with anubis.proposal.ProposalSaver(call=call,
                                               user=flask.g.current_user) as saver:
                pass
            return flask.redirect(
                flask.url_for('proposal.edit', pid=saver.doc['identifier']))

@blueprint.route('/<cid>.zip')
def call_zip(cid):
    """Return a zip file containing the XLSX for all submitted proposals
    and all documents attached to those proposals.
    """
    call = get_call(cid)
    if not call:
        return utils.error('No such call.', flask.url_for('home'))
    if not allow_view_details(call):
        return utils.error('You are not allowed to view the call details.')
    proposals = anubis.proposals.get_call_proposals(call, submitted=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zip:
        zip.writestr(f"{call['identifier']}_proposals.xlsx",
                     anubis.proposals.get_call_xlsx(call, submitted=True))
        for proposal in proposals:
            for field in call['proposal']:
                if field['type'] == constants.DOCUMENT:
                    try:
                        doc = anubis.proposal.get_document(proposal,
                                                           field['identifier'])
                    except KeyError:
                        pass
                    else:
                        zip.writestr(doc['filename'], doc['content'])
    response = flask.make_response(output.getvalue())
    response.headers.set('Content-Type', constants.ZIP_MIMETYPE)
    response.headers.set('Content-Disposition', 'attachment', 
                         filename=f"{call['identifier']}.zip")
    return response


class CallSaver(AttachmentSaver):
    "Call document saver context."

    DOCTYPE = constants.CALL

    def initialize(self):
        self.doc['owner'] = flask.g.current_user['username']
        self.doc['opens'] = None
        self.doc['closes'] = None
        self.doc['proposal'] = []
        self.doc['documents'] = []
        self.doc['review'] = []
        self.doc['reviewers'] = []
        self.doc['chairs'] = []
        self.doc['access'] = {k: False for k in constants.ACCESS}
        self.doc['decision'] = []

    def set_identifier(self, identifier):
        "Call identifier."
        if self.doc.get('identifier'):
            raise ValueError('Identifier has already been set.')
        if not identifier:
            raise ValueError('Identifier must be provided.')
        if len(identifier) > flask.current_app.config['CALL_IDENTIFIER_MAXLENGTH']:
            raise ValueError('Too long identifier.')
        if not constants.ID_RX.match(identifier):
            raise ValueError('Invalid identifier.')
        if get_call(identifier):
            raise ValueError('Identifier is already in use.')
        self.doc['identifier'] = identifier

    def set_title(self, title):
        "Call title: non-blank required."
        title = title.strip()
        if not title:
            raise ValueError('Title must be provided.')
        self.doc['title'] = title

    def add_field(self, form):
        "Get the field definition from the form."
        type = form.get('type')
        if type not in constants.FIELD_TYPES:
            raise ValueError('Invalid field type.')
        fid = form.get('identifier')
        if not (fid and constants.ID_RX.match(fid)):
            raise ValueError('Invalid field identifier.')
        title = form.get('title')
        if not title:
            title = ' '.join([w.capitalize()
                              for w in fid.replace('_', ' ').split()])
        field = {'type': type,
                 'identifier': fid,
                 'title': title,
                 'description': form.get('description') or None,
                 'required': bool(form.get('required')),
                 'banner': bool(form.get('banner'))
                 }
        if type in (constants.TEXT, constants.LINE):
            try:
                maxlength = int(form.get('maxlength'))
                if maxlength <= 0: raise ValueError
            except (TypeError, ValueError):
                maxlength = None
            field['maxlength'] = maxlength

        elif type == constants.INTEGER:
            try:
                minimum = int(form.get('minimum'))
            except (TypeError, ValueError):
                minimum = None
            try:
                maximum = int(form.get('maximum'))
            except (TypeError, ValueError):
                maximum = None
            if minimum is not None and maximum is not None and maximum <= minimum:
                raise ValueError('Invalid score range.')
            field['minimum'] = minimum
            field['maximum'] = maximum

        elif type == constants.FLOAT:
            try:
                minimum = float(form.get('minimum'))
            except (TypeError, ValueError):
                minimum = None
            try:
                maximum = float(form.get('maximum'))
            except (TypeError, ValueError):
                maximum = None
            if minimum is not None and maximum is not None and maximum <= minimum:
                raise ValueError('Invalid score range.')
            field['minimum'] = minimum
            field['maximum'] = maximum

        elif type == constants.SCORE:
            try:
                minimum = int(form.get('minimum'))
            except (TypeError, ValueError):
                minimum = None
            try:
                maximum = int(form.get('maximum'))
            except (TypeError, ValueError):
                maximum = None
            if minimum is None or maximum is None or maximum <= minimum:
                raise ValueError('Invalid score range.')
            field['minimum'] = minimum
            field['maximum'] = maximum
            field['slider'] = utils.to_bool(form.get('slider'))

        elif type == constants.SELECT:
            field['multiple'] = bool(form.get('multiple'))
            field['selection'] = [s.strip() for s in
                                  form.get('selection', '').split('\n')]

        return field

    def edit_field(self, fieldlist, fid, form):
        "Edit or move the field definition using data in the form."
        for pos, field in enumerate(fieldlist):
            if field['identifier'] == fid:
                break
        else:
            raise KeyError('No such decision field.')
        move = form.get('_move')
        if move == 'up':
            fieldlist.pop(pos)
            if pos == 0:
                fieldist.append(field)
            else:
                fieldlist.insert(pos-1, field)
        else:
            self.edit_field_definition(field, form)

    def edit_field_definition(self, field, form):
        "Edit the field definition from the form."
        title = form.get('title')
        if not title:
            title = ' '.join([w.capitalize() 
                              for w in 
                              field['identifier'].replace('_', ' ').split()])
        field['title'] = title
        field['description'] = form.get('description') or None
        field['required'] = bool(form.get('required'))
        field['banner'] = bool(form.get('banner'))

        if field['type'] in (constants.TEXT, constants.LINE):
            try:
                maxlength = int(form.get('maxlength'))
                if maxlength <= 0: raise ValueError
            except (TypeError, ValueError):
                maxlength = None
            field['maxlength'] = maxlength

        elif field['type'] == constants.INTEGER:
            try:
                minimum = int(form.get('minimum'))
            except (TypeError, ValueError):
                minimum = None
            field['minimum'] = minimum
            try:
                maximum = int(form.get('maximum'))
            except (TypeError, ValueError):
                maximum = None
            field['maximum'] = maximum

        elif field['type'] == constants.FLOAT:
            try:
                minimum = float(form.get('minimum'))
            except (TypeError, ValueError):
                minimum = None
            field['minimum'] = minimum
            try:
                maximum = float(form.get('maximum'))
            except (TypeError, ValueError):
                maximum = None
            field['maximum'] = maximum

        elif field['type'] == constants.SCORE:
            try:
                minimum = int(form.get('minimum'))
            except (TypeError, ValueError):
                minimum = None
            try:
                maximum = int(form.get('maximum'))
            except (TypeError, ValueError):
                maximum = None
            if minimum is None or maximum is None or maximum <= minimum:
                raise ValueError('Invalid score range.')
            field['minimum'] = minimum
            field['maximum'] = maximum
            field['slider'] = utils.to_bool(form.get('slider'))

        elif field['type'] == constants.SELECT:
            field['multiple'] = bool(form.get('multiple'))
            field['selection'] = [s.strip() for s in
                                  form.get('selection', '').split('\n')]

    def add_proposal_field(self, form):
        "Add a field to the proposal definition."
        field = self.add_field(form)
        if field['identifier'] in [f['identifier'] 
                                   for f in self.doc['proposal']]:
            raise ValueError('Field identifier is already in use.')
        self.doc['proposal'].append(field)

    def edit_proposal_field(self, fid, form):
        "Edit the field for the proposal definition."
        self.edit_field(self.doc['proposal'], fid, form)

    def delete_proposal_field(self, fid):
        "Delete the given field from proposal definition."
        for pos, field in enumerate(self.doc['proposal']):
            if field['identifier'] == fid:
                self.doc['proposal'].pop(pos)
                break
        else:
            raise ValueError('No such proposal field.')

    def add_review_field(self, form):
        "Add a field to the review definition."
        field = self.add_field(form)
        if field['identifier'] in [f['identifier'] for f in self.doc['review']]:
            raise ValueError('Field identifier is already in use.')
        self.doc['review'].append(field)

    def edit_review_field(self, fid, form):
        "Edit the review definition field."
        self.edit_field(self.doc['review'], fid, form)

    def delete_review_field(self, fid):
        "Delete the field from the review definition."
        for pos, field in enumerate(self.doc['review']):
            if field['identifier'] == fid:
                self.doc['review'].pop(pos)
                break
        else:
            raise ValueError('No such review field.')

    def add_decision_field(self, form):
        "Add a field to the decision definition."
        field = self.add_field(form)
        if field['identifier'] in [f['identifier'] for f in self.doc['decision']]:
            raise ValueError('Field identifier is already in use.')
        self.doc['decision'].append(field)

    def edit_decision_field(self, fid, form):
        "Edit the decision definition field."
        self.edit_field(self.doc['decision'], fid, form)

    def delete_decision_field(self, fid):
        "Delete the field from the decision definition."
        for pos, field in enumerate(self.doc['decision']):
            if field['identifier'] == fid:
                self.doc['decision'].pop(pos)
                break
        else:
            raise ValueError('No such decision field.')

    def add_document(self, infile, description):
        "Add a document to the call."
        self.add_attachment(infile.filename,
                            infile.read(),
                            infile.mimetype)
        for document in self.doc['documents']:
            if document['name'] == infile.filename:
                document['description'] = description
                break
        else:
            self.doc['documents'].append({'name': infile.filename,
                                          'description': description})

    def delete_document(self, documentname):
        "Delete the named document from the call."
        for pos, document in enumerate(self.doc['documents']):
            if document['name'] == documentname:
                self.delete_attachment(documentname)
                self.doc['documents'].pop(pos)
                break

    def edit_access(self, form):
        "Edit the access flags."
        self.doc['access'] = {}
        for flag in constants.ACCESS:
            self.doc['access'][flag] = utils.to_bool(form.get(flag))

    def set_categories(self, form):
        "Set the categories for the proposals in the call."
        values = [v.strip() for v in form.get("categories").split("\n")]
        values = [v for v in values if v]
        self.doc["categories"] = values


def get_call(cid):
    "Return the call with the given identifier."
    try:
        return flask.g.cache[cid]
    except KeyError:
        result = [r.doc for r in flask.g.db.view('calls', 'identifier',
                                                 key=cid,
                                                 include_docs=True)]
        if len(result) == 1:
            call = set_tmp(result[0])
            flask.g.cache[cid] = call
            flask.g.cache[call["_id"]] = call
            return call
        else:
            return None

def allow_create(user=None):
    "Allow admin and user's with 'call_creator' flag set to create a call."
    if user is None:
        user = flask.g.current_user
    if not user: return False
    return user.get('call_creator') or user['role'] == constants.ADMIN

def allow_view(call):
    """The admin, staff and call owner may view any call.
    Others may view a call if it has an opens date.
    """
    if flask.g.am_admin: return True
    if flask.g.am_staff: return True
    if am_call_owner(call): return True
    return bool(call['opens'])

def allow_edit(call):
    "The admin and call owner may edit a call."
    return flask.g.am_admin or am_call_owner(call)

def allow_delete(call):
    "Allow the admin or call owner to delete a call if it has no proposals."
    if not (flask.g.am_admin or am_call_owner(call)): return False
    return utils.get_count('proposals', 'call', call['identifier']) == 0

def allow_proposal(call):
    "Any logged-in user, except reviewer, may create a proposal."
    if not flask.g.current_user: return False
    return not am_reviewer(call)

def allow_view_details(call):
    """The admin, staff, call owner and reviewers may view the details
    of the call, including all proposals.
    """
    if not flask.g.current_user: return False
    if flask.g.am_admin: return True
    if flask.g.am_staff: return True
    if am_call_owner(call): return True
    return am_reviewer(call)

def allow_view_reviews(call):
    """The admin, staff and call owner may view all reviews in the call.
    Review chairs may view all reviews.
    Other reviewers may view depending on the access flag for the call.
    """
    if not flask.g.current_user: return False
    if flask.g.am_admin: return True
    if flask.g.am_staff: return True
    if am_call_owner(call): return True
    if am_reviewer(call):
        if am_chair(call): return True
        return bool(call['access'].get('allow_reviewer_view_all_reviews'))
    return False

def allow_view_decisions(call):
    """The admin, staff and call owner may view all decisions in the call.
    Reviewer may view all decisions in a call once the review
    due date has passed; this should reduce confusion.
    """
    if not flask.g.current_user: return False
    if flask.g.am_admin: return True
    if flask.g.am_staff: return True
    if am_call_owner(call): return True
    due = call.get('reviews_due')
    if due:
        return am_reviewer(call) and utils.normalized_local_now() > due
    return False

def am_reviewer(call):
    "Is the current user a reviewer in the call?"
    if not flask.g.current_user: return False
    return flask.g.current_user['username'] in call['reviewers']

def am_chair(call):
    "Is the current user a chair in the call?"
    if not flask.g.current_user: return False
    return flask.g.current_user['username'] in call['chairs']

def am_call_owner(call):
    "Is the current user the owner of the call?"
    if not flask.g.current_user: return False
    return flask.g.current_user['username'] == call['owner']

def set_tmp(call):
    """Set the temporary, non-saved values for the call.
    Returns the call object.
    """
    tmp = {}
    # Set the current state of the call, computed from open/close and today.
    if call['opens']:
        if call['opens'] > utils.normalized_local_now():
            tmp['is_open'] = False
            tmp['is_closed'] = False
            tmp['text'] = 'Not yet open.'
            tmp['color'] = 'secondary'
        elif call['closes']:
            remaining = utils.days_remaining(call['closes'])
            if remaining > 7:
                tmp['is_open'] = True
                tmp['is_closed'] = False
                tmp['text'] = f"{remaining:.0f} days remaining."
                tmp['color'] = 'success'
            elif remaining >= 2:
                tmp['is_open'] = True
                tmp['is_closed'] = False
                tmp['text'] = f"{remaining:.0f} days remaining."
                tmp['color'] = 'warning'
            elif remaining >= 0:
                tmp['is_open'] = True
                tmp['is_closed'] = False
                tmp['text'] = f"{remaining:.1f} days remaining."
                tmp['color'] = 'danger'
            else:
                tmp['is_open'] = False
                tmp['is_closed'] = True
                tmp['text'] = 'Closed.'
                tmp['color'] = 'dark'
        else:
            tmp['is_open'] = True
            tmp['is_closed'] = False
            tmp['text'] = 'Open with no closing date.'
            tmp['color'] = 'success'
    else:
        if call['closes']:
            tmp['is_open'] = False
            tmp['is_closed'] = False
            tmp['text'] = 'No open date set.'
            tmp['color'] = 'secondary'
        else:
            tmp['is_open'] = False
            tmp['is_closed'] = False
            tmp['text'] = 'No open or close dates set.'
            tmp['color'] = 'secondary'
    tmp['is_published'] = tmp['is_open'] or tmp['is_closed']
    call['tmp'] = tmp
    return call

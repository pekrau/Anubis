"Lists of calls for proposals."

import flask

import anubis.call

from . import constants
from . import utils


blueprint = flask.Blueprint('calls', __name__)

@blueprint.route('')
@utils.login_required
def all():
    """All calls.
    Includes calls that have not been opened,
    and those with neither opens nor closes dates set.
    """
    if not (flask.g.am_admin or flask.g.am_staff):
        return utils.error('You are not allowed to view all calls.')
    calls = [anubis.call.set_tmp(r.doc) for r in 
             flask.g.db.view('calls', 'identifier', include_docs=True)]
    return flask.render_template('calls/all.html', calls=calls)

@blueprint.route('/owner/<username>')
@utils.login_required
def owner(username):
    """Calls owned by the given user.
    Includes calls that have not been opened,
    and those with neither opens nor closes dates set.
    """
    calls = [anubis.call.set_tmp(r.doc) for r in 
             flask.g.db.view('calls', 'owner',
                             key=username,
                             reduce=False,
                             include_docs=True)]
    return flask.render_template('calls/owner.html',
                                 calls=calls,
                                 username=username,
                                 allow_create=anubis.call.allow_create())

@blueprint.route('/closed')
def closed():
    "Closed calls."
    calls = [anubis.call.set_tmp(r.doc) 
             for r in flask.g.db.view('calls', 'closes', 
                                      startkey='',
                                      endkey=utils.normalized_local_now(),
                                      include_docs=True)]
    return flask.render_template('calls/closed.html',
                                 calls=calls,
                                 am_call_owner=anubis.call.am_call_owner,
                                 allow_create=anubis.call.allow_create())

@blueprint.route('/open')
def open():
    "Open calls."
    return flask.render_template('calls/open.html',
                                 calls=get_open_calls(),
                                 am_call_owner=anubis.call.am_call_owner,
                                 allow_create=anubis.call.allow_create())

def get_open_calls():
    "Return list of open calls, sorted according to configuration."
    limited = [anubis.call.set_tmp(r.doc)
               for r in flask.g.db.view('calls', 'closes', 
                                        startkey=utils.normalized_local_now(),
                                        endkey='ZZZZZZ',
                                        include_docs=True)]
    open_ended = [anubis.call.set_tmp(r.doc)
                  for r in flask.g.db.view('calls', 'open_ended', 
                                           startkey='',
                                           endkey=utils.normalized_local_now(),
                                           include_docs=True)]
    order_key = flask.current_app.config['CALLS_OPEN_ORDER_KEY']
    if order_key == 'closes':
        limited.sort(key=lambda k: (k['closes'], k['title']))
        open_ended.sort(key=lambda k: k['title'])
        result = limited + open_ended
    elif order_key == 'title':
        result = limited + open_ended
        result.sort(key=lambda k: k['title'])
    elif order_key == 'identifier':
        result = limited + open_ended
        result.sort(key=lambda k: k['identifier'])
    else:
        result = limited + open_ended
        result.sort(key=lambda k: k['identifier'])
    return result

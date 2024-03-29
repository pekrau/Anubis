"Flask web app creation and setup; main entry point."

import functools
import http.client
import io

import couchdb2
import flask
import markupsafe

import anubis.api
import anubis.database
import anubis.display
import anubis.call
import anubis.calls
import anubis.config
import anubis.review
import anubis.reviews
import anubis.proposal
import anubis.proposals
import anubis.decision
import anubis.grant
import anubis.grants
import anubis.about
import anubis.admin
import anubis.user

from anubis import constants
from anubis import utils


# Create the global Flask app with main configuration.
app = anubis.config.create_app()

# Further configuration for the web app.
anubis.display.init(app)


@app.before_request
def prepare():
    "Set the database connection, get the current user."
    flask.g.db = anubis.database.get_db()
    flask.g.current_user = anubis.user.get_current_user()
    flask.g.am_admin = anubis.user.am_admin()
    flask.g.am_staff = anubis.user.am_staff()
    try:
        flask.g.alert_text = flask.g.db["alert"]["text"]
    except couchdb2.NotFoundError:
        flask.g.alert_text = None
    if flask.g.current_user:
        username = flask.g.current_user["username"]
        flask.g.allow_create_call = anubis.call.allow_create()
        flask.g.my_proposals_count = anubis.database.get_count(
            "proposals", "user", username
        )
        flask.g.my_unsubmitted_proposals_count = anubis.database.get_count(
            "proposals", "unsubmitted", username
        )
        flask.g.my_reviews_count = anubis.database.get_count(
            "reviews", "reviewer", username
        )
        flask.g.my_unfinalized_reviews_count = anubis.database.get_count(
            "reviews", "unfinalized", username
        )
        flask.g.my_grants_count = anubis.database.get_count(
            "grants", "user", username
        ) + anubis.database.get_count("grants", "access", username)
        flask.g.my_incomplete_grants_count = anubis.database.get_count(
            "grants", "incomplete", username
        )
        flask.g.orcid_require = (
            flask.current_app.config.get("USER_REQUEST_ORCID")
            and not (flask.g.am_admin or flask.g.am_staff)
            and not flask.g.current_user.get("orcid")
        )


@app.route("/")
def home():
    "Home page."
    # The list is already properly sorted.
    return flask.render_template(
        "home.html",
        calls=anubis.calls.get_open_calls(),
        allow_create_call=anubis.call.allow_create(),
    )


@app.route("/search")
@utils.login_required
def search():
    """Search proposals:
    - identifier (exact)
    - title (terms)
    """
    proposals = {}

    parts = flask.request.args.get("term", "").split()
    parts = [p for p in parts if p]

    # Exact proposal identifier.
    for part in parts:
        proposal = anubis.proposal.get_proposal(part.upper())
        if proposal:
            proposals[proposal["identifier"]] = proposal

    # Exact proposal IUIDs.
    for part in parts:
        proposal = anubis.database.get_doc(part.lower())
        if proposal and proposal["doctype"] == constants.PROPOSAL:
            proposals[proposal["identifier"]] = proposal

    # Search proposal titles for parts.
    term = (
        "".join(
            [
                c in constants.PROPOSALS_SEARCH_DELIMS_LINT and " " or c
                for c in flask.request.args.get("term", "")
            ]
        )
        .strip()
        .lower()
    )
    parts = [
        part
        for part in term.split()
        if part and len(part) >= 2 and part not in constants.PROPOSALS_SEARCH_LINT
    ]

    id_sets = []
    for part in parts:
        id_sets.append(
            set(
                [
                    row.id
                    for row in flask.g.db.view(
                        "proposals",
                        "term",
                        startkey=part,
                        endkey=part + constants.CEILING,
                    )
                ]
            )
        )

    # All term parts (=words) must exist in the title.
    if id_sets:
        ids = functools.reduce(lambda i, j: i.intersection(j), id_sets)
        for proposal in flask.g.db.get_bulk(ids):
            proposals[proposal["identifier"]] = proposal

    # Seletc those proposals which the current user may view.
    proposals = [
        proposal
        for proposal in proposals.values()
        if anubis.proposal.allow_view(proposal)
    ]
    proposals.sort(key=lambda p: p.get("submitted") or "-", reverse=True)
    return flask.render_template(
        "search.html", proposals=proposals, term=flask.request.args.get("term", "")
    )


@app.route("/documentation")
def documentation():
    "Documentation page; the prepocessed file 'documentation.md'."
    return flask.render_template("documentation.html")


@app.route("/status")
def status():
    "Return JSON for the current status and some counts for the database."
    result = dict(status="ok")
    result.update(anubis.database.get_counts())
    return result


@app.route("/site/<filename>")
def site(filename):
    if filename in constants.SITE_FILES:
        try:
            filedata = flask.current_app.config[f"SITE_{filename.upper()}"]
            return flask.send_file(
                io.BytesIO(filedata["content"]),
                mimetype=filedata["mimetype"],
                etag=filedata["etag"],
                last_modified=filedata["modified"],
                max_age=constants.SITE_FILE_MAX_AGE,
            )
        except KeyError:
            pass
    flask.abort(http.client.NOT_FOUND)


@app.route("/sitemap")
def sitemap():
    "Return an XML sitemap."
    pages = [
        dict(url=flask.url_for("home", _external=True)),
        dict(url=flask.url_for("about.contact", _external=True)),
        dict(url=flask.url_for("about.software", _external=True)),
        dict(url=flask.url_for("calls.open", _external=True)),
        dict(url=flask.url_for("calls.closed", _external=True)),
    ]
    for call in anubis.calls.get_open_calls():
        pages.append(
            dict(
                url=flask.url_for(
                    "call.display", cid=call["identifier"], _external=True
                )
            )
        )
    xml = flask.render_template("sitemap.xml", pages=pages)
    response = flask.current_app.make_response(xml)
    response.mimetype = constants.XML_MIMETYPE
    return response


# Set up the URL map.
app.register_blueprint(anubis.user.blueprint, url_prefix="/user")
app.register_blueprint(anubis.call.blueprint, url_prefix="/call")
app.register_blueprint(anubis.calls.blueprint, url_prefix="/calls")
app.register_blueprint(anubis.proposal.blueprint, url_prefix="/proposal")
app.register_blueprint(anubis.proposals.blueprint, url_prefix="/proposals")
app.register_blueprint(anubis.review.blueprint, url_prefix="/review")
app.register_blueprint(anubis.reviews.blueprint, url_prefix="/reviews")
app.register_blueprint(anubis.decision.blueprint, url_prefix="/decision")
app.register_blueprint(anubis.grant.blueprint, url_prefix="/grant")
app.register_blueprint(anubis.grants.blueprint, url_prefix="/grants")
app.register_blueprint(anubis.about.blueprint, url_prefix="/about")
app.register_blueprint(anubis.admin.blueprint, url_prefix="/admin")
app.register_blueprint(anubis.api.blueprint, url_prefix="/api")


@app.route("/<path:path>")
def catchall(path):
    "Catch URL that doesn't match anything. Has to be added as the last item."
    utils.flash_error(f"No such page: '/{path}'")
    return flask.redirect(flask.url_for("home"))


# This part is used only during development.
if __name__ == "__main__":
    app.run()

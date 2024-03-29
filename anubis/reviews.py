"Lists of reviews."

import io
import zipfile

import flask
import xlsxwriter

import anubis.call
import anubis.database
import anubis.proposal
import anubis.proposals
import anubis.review
import anubis.user
from anubis import constants
from anubis import utils

blueprint = flask.Blueprint("reviews", __name__)


@blueprint.route("/create/<username>", methods=["POST"])
@utils.login_required
def create(username):
    """Create new reviews for the given proposals for the given reviewer.
    The proposals must be in the given call.
    """
    user = anubis.user.get_user(username=username)
    if user is None:
        return utils.error(f"No such user '{username}'.")
    cid = flask.request.form.get("cid")
    if not cid:
        return utils.error("No call given.")
    call = anubis.call.get_call(cid)
    if not call:
        return utils.error(f"No such call '{cid}'.")

    try:
        if user["username"] not in call["reviewers"]:
            raise ValueError(f"User is not a reviewer in the call '{cid}'.")
        if utils.to_bool(flask.request.form.get("every", None)):
            pidlist = [prop["identifier"] for prop in
                       anubis.proposals.get_call_proposals(call, submitted=True)
                       if anubis.review.get_reviewer_review(prop, user) is None]
        else:
            pidlist = flask.request.form.getlist("pid")
        for pid in pidlist:
            proposal = anubis.proposal.get_proposal(pid)
            if proposal is None:
                continue
            if not anubis.review.allow_create(proposal):
                raise ValueError(
                    f"You may not create a review for the proposal '{pid}'."
                )
            if user["username"] == proposal["user"]:
                raise ValueError(
                    f"Reviewer not allowed to review their own proposal '{pid}'."
                )

            review = anubis.review.get_reviewer_review(proposal, user)
            if review is None:
                with anubis.review.ReviewSaver(proposal=proposal, user=user) as saver:
                    pass
            else:
                utils.flash_message(f"The review for '{pid}' already exists.")
    except ValueError as error:
        utils.flash_error(error)
    return flask.redirect(
        flask.url_for("reviews.call_reviewer", cid=cid, username=username)
    )


@blueprint.route("/call/<cid>")
@utils.login_required
def call(cid):
    "List all reviews for a call."
    call = anubis.call.get_call(cid)
    if call is None:
        return utils.error("No such call.")
    if not anubis.call.allow_view(call):
        return utils.error("You may not view the call.")
    if not anubis.call.allow_view_reviews(call):
        return utils.error(
            "You may not view the reviews of the call.",
            flask.url_for("call.display", cid=call["identifier"]),
        )

    proposals = anubis.proposals.get_call_proposals(call, submitted=True)
    for proposal in proposals:
        proposal["allow_create_review"] = anubis.review.allow_create(proposal)
    reviews = anubis.database.get_docs("reviews", "call", call["identifier"])
    # For ordinary reviewer, list only finalized, non-conflict-of-interest reviews.
    if flask.g.am_admin or flask.g.am_staff or anubis.call.am_chair(call):
        only_finalized = False
    else:
        reviews = [r for r in reviews if r.get("finalized")]
        reviews = [r for r in reviews if not r["values"].get("conflict_of_interest")]
        only_finalized = True
    reviews_lookup = {f"{r['proposal']} {r['reviewer']}": r for r in reviews}
    return flask.render_template(
        "reviews/call.html",
        call=call,
        proposals=proposals,
        reviews_lookup=reviews_lookup,
        only_finalized=only_finalized,
    )


@blueprint.route("/call/<cid>.xlsx")
@utils.login_required
def call_xlsx(cid):
    "Produce an XLSX file of all reviews for a call."
    call = anubis.call.get_call(cid)
    if call is None:
        return utils.error("No such call.")
    if not anubis.call.allow_view(call):
        return utils.error("You may not view the call.")
    if not anubis.call.allow_view_reviews(call):
        return utils.error(
            "You may not view the reviews of the call.",
            flask.url_for("call.display", cid=call["identifier"]),
        )

    proposals = anubis.proposals.get_call_proposals(call, submitted=True)
    reviews = anubis.database.get_docs("reviews", "call", call["identifier"])
    # For ordinary reviewer, list only finalized reviews.
    if not (flask.g.am_admin or anubis.call.am_chair(call)):
        reviews = [
            r
            for r in reviews
            if r["reviewer"] != flask.g.current_user["username"] and r.get("finalized")
        ]
    reviews_lookup = {f"{r['proposal']} {r['reviewer']}": r for r in reviews}
    content = get_reviews_xlsx(call, proposals, reviews_lookup)
    response = flask.make_response(content)
    response.headers.set("Content-Type", constants.XLSX_MIMETYPE)
    response.headers.set(
        "Content-Disposition", "attachment", filename=f"{cid}_reviews.xlsx"
    )
    return response


@blueprint.route("/call/<cid>/reviewer/<username>")
@utils.login_required
def call_reviewer(cid, username):
    "List all reviews in the call by the reviewer (user)."
    call = anubis.call.get_call(cid)
    if call is None:
        return utils.error("No such call.")
    user = anubis.user.get_user(username=username)
    if user is None:
        return utils.error("No such user.")
    if user["username"] not in call["reviewers"]:
        return utils.error("The user is not a reviewer in the call.")
    if not (
        user["username"] == flask.g.current_user["username"]
        or anubis.call.allow_view_reviews(call)
    ):
        return utils.error(
            "You may not view the user's reviews.",
            flask.url_for("call.display", cid=call["identifier"]),
        )

    proposals = anubis.proposals.get_call_proposals(call, submitted=True)
    for proposal in proposals:
        proposal["allow_create_review"] = anubis.review.allow_create(proposal)
    reviews = anubis.database.get_docs(
        "reviews", "call_reviewer", [call["identifier"], user["username"]]
    )
    reviews_lookup = {r["proposal"]: r for r in reviews}
    return flask.render_template(
        "reviews/call_reviewer.html",
        call=call,
        proposals=proposals,
        user=user,
        reviews_lookup=reviews_lookup,
        rank_error=anubis.reviews.get_rank_error(call, username),
    )


@blueprint.route("/call/<cid>/reviewer/<username>.xlsx")
@utils.login_required
def call_reviewer_xlsx(cid, username):
    "Produce an XLSX file of all reviews in the call by the reviewer (user)."
    call = anubis.call.get_call(cid)
    if call is None:
        return utils.error("No such call.")
    user = anubis.user.get_user(username=username)
    if user is None:
        return utils.error("No such user.")
    if user["username"] not in call["reviewers"]:
        return utils.error("The user is not a reviewer in the call.")
    if not (
        user["username"] == flask.g.current_user["username"]
        or anubis.call.allow_view_reviews(call)
    ):
        return utils.error(
            "You may not view the user's reviews.",
            flask.url_for("call.display", cid=call["identifier"]),
        )

    proposals = anubis.proposals.get_call_proposals(call, submitted=True)
    reviews = anubis.database.get_docs(
        "reviews", "call_reviewer", [call["identifier"], user["username"]]
    )
    reviews_lookup = {f"{r['proposal']} {username}": r for r in reviews}
    content = get_reviews_xlsx(call, proposals, reviews_lookup)
    response = flask.make_response(content)
    response.headers.set("Content-Type", constants.XLSX_MIMETYPE)
    response.headers.set(
        "Content-Disposition", "attachment", filename=f"{cid}_{username}_reviews.xlsx"
    )
    return response


@blueprint.route("/call/<cid>/reviewer/<username>.zip")
@utils.login_required
def call_reviewer_zip(cid, username):
    """Return a zip file containing the XLSX file of all reviews
    in the call by the reviewer (user), and all documents for the proposals
    to be reviewed.
    """
    call = anubis.call.get_call(cid)
    if call is None:
        return utils.error("No such call.")
    user = anubis.user.get_user(username=username)
    if user is None:
        return utils.error("No such user.")
    if user["username"] not in call["reviewers"]:
        return utils.error("The user is not a reviewer in the call.")
    if not (
        user["username"] == flask.g.current_user["username"]
        or anubis.call.allow_view_reviews(call)
    ):
        return utils.error(
            "You may not view the user's reviews.",
            flask.url_for("call.display", cid=call["identifier"]),
        )

    proposals = anubis.proposals.get_call_proposals(call, submitted=True)
    reviews = anubis.database.get_docs(
        "reviews", "call_reviewer", [call["identifier"], user["username"]]
    )
    reviews_lookup = {f"{r['proposal']} {username}": r for r in reviews}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zip:
        zip.writestr(
            f"{cid}_{username}_reviews.xlsx",
            get_reviews_xlsx(call, proposals, reviews_lookup),
        )
        # Filter away proposals not to be reviewed by the user.
        proposals = [
            p for p in proposals if f"{p['identifier']} {username}" in reviews_lookup
        ]
        zip.writestr(
            f"{call['identifier']}_proposals_to_review.xlsx",
            anubis.proposals.get_call_xlsx(call, proposals=proposals),
        )
        for proposal in proposals:
            for field in call["proposal"]:
                if field["type"] == constants.DOCUMENT:
                    try:
                        doc = anubis.proposal.get_document(
                            proposal, field["identifier"]
                        )
                    except KeyError:
                        pass
                    else:
                        zip.writestr(doc["filename"], doc["content"])
    response = flask.make_response(output.getvalue())
    response.headers.set("Content-Type", constants.ZIP_MIMETYPE)
    response.headers.set(
        "Content-Disposition",
        "attachment",
        filename=f"{call['identifier']}_reviewer_{username}.zip",
    )
    return response


@blueprint.route("/proposal/<pid>")
@utils.login_required
def proposal(pid):
    "List all reviewers and reviews for a proposal."
    proposal = anubis.proposal.get_proposal(pid)
    if proposal is None:
        return utils.error("No such proposal.")

    call = anubis.call.get_call(proposal["call"])
    if not anubis.call.allow_view_reviews(call):
        return utils.error(
            "You may not view the reviews of the call.",
            flask.url_for("call.display", cid=call["identifier"]),
        )

    reviews = anubis.database.get_docs("reviews", "proposal", proposal["identifier"])
    # For ordinary reviewer, list only finalized reviews.
    if flask.g.am_admin or flask.g.am_staff or anubis.call.am_chair(call):
        only_finalized = False
    else:
        only_finalized = True
        reviews = [r for r in reviews if r.get("finalized")]
    allow_create_review = anubis.review.allow_create(proposal)
    reviews_lookup = {r["reviewer"]: r for r in reviews}
    return flask.render_template(
        "reviews/proposal.html",
        proposal=proposal,
        call=call,
        allow_create_review=allow_create_review,
        reviewers=call["reviewers"],
        reviews_lookup=reviews_lookup,
        only_finalized=only_finalized,
    )


@blueprint.route("/proposal/<pid>/archived")
@utils.login_required
def proposal_archived(pid):
    "List all archived reviews for a proposal."
    proposal = anubis.proposal.get_proposal(pid)
    if proposal is None:
        return utils.error("No such proposal.")

    call = anubis.call.get_call(proposal["call"])
    if not (flask.g.am_admin or anubis.call.am_chair(call)):
        return utils.error(
            "You may not view the archived reviews of the call.",
            flask.url_for("call.display", cid=call["identifier"]),
        )

    reviews = anubis.database.get_docs(
        "reviews", "proposal_archived", proposal["identifier"]
    )
    return flask.render_template(
        "reviews/proposal_archived.html", reviews=reviews, proposal=proposal, call=call
    )


@blueprint.route("/call/<cid>/archived")
@utils.login_required
def call_archived(cid):
    "List all archived reviews in the call."
    call = anubis.call.get_call(cid)
    if call is None:
        return utils.error("No such call.")
    if not anubis.call.allow_view_reviews(call):
        return utils.error(
            "You may not view the call's reviews.",
            flask.url_for("call.display", cid=call["identifier"]),
        )

    proposals = anubis.proposals.get_call_proposals(call, submitted=True)
    reviews = [
        r.doc
        for r in flask.g.db.view(
            "reviews",
            "call_reviewer_archived",
            startkey=[call["identifier"], ""],
            endkey=[call["identifier"], constants.CEILING],
            include_docs=True,
        )
    ]
    reviews_lookup = {r["proposal"]: r for r in reviews}
    return flask.render_template(
        "reviews/call_archived.html",
        call=call,
        proposals=proposals,
        reviews_lookup=reviews_lookup,
    )


@blueprint.route("/call/<cid>/reviewer/<username>/archived")
@utils.login_required
def call_reviewer_archived(cid, username):
    "List all archived reviews in the call by the reviewer (user)."
    call = anubis.call.get_call(cid)
    if call is None:
        return utils.error("No such call.")
    user = anubis.user.get_user(username=username)
    if user is None:
        return utils.error("No such user.")
    if user["username"] not in call["reviewers"]:
        return utils.error("The user is not a reviewer in the call.")
    if not (
        user["username"] == flask.g.current_user["username"]
        or anubis.call.allow_view_reviews(call)
    ):
        return utils.error(
            "You may not view the user's reviews.",
            flask.url_for("call.display", cid=call["identifier"]),
        )

    proposals = anubis.proposals.get_call_proposals(call, submitted=True)
    reviews = anubis.database.get_docs(
        "reviews", "call_reviewer_archived", [call["identifier"], user["username"]]
    )
    reviews_lookup = {r["proposal"]: r for r in reviews}
    return flask.render_template(
        "reviews/call_reviewer_archived.html",
        call=call,
        proposals=proposals,
        user=user,
        reviews_lookup=reviews_lookup,
    )


@blueprint.route("/proposal/<pid>.xlsx")
@utils.login_required
def proposal_xlsx(pid):
    "Produce an XLSX file of all reviewers and reviews for a proposal."
    proposal = anubis.proposal.get_proposal(pid)
    if proposal is None:
        return utils.error("No such proposal.")
    if not anubis.proposal.allow_view(proposal):
        return utils.error("You may not view the proposal.")

    call = anubis.call.get_call(proposal["call"])
    if not anubis.call.allow_view_reviews(call):
        return utils.error("You may not view the reviews of the call.")

    reviews = anubis.database.get_docs("reviews", "proposal", proposal["identifier"])
    if not (flask.g.am_admin or anubis.call.am_chair(call)):
        reviews = [
            r
            for r in reviews
            if r["reviewer"] != flask.g.current_user["username"] and r.get("finalized")
        ]
    reviews_lookup = {f"{pid} {r['reviewer']}": r for r in reviews}
    content = get_reviews_xlsx(call, [proposal], reviews_lookup)
    response = flask.make_response(content)
    response.headers.set("Content-Type", constants.XLSX_MIMETYPE)
    response.headers.set(
        "Content-Disposition", "attachment", filename=f"{pid}_reviews.xlsx"
    )
    return response


@blueprint.route("/reviewer/<username>")
@utils.login_required
def reviewer(username):
    """List all reviews by the given reviewer (user).
    If the user is reviewer in only one call, redirect to that page.
    """
    user = anubis.user.get_user(username=username)
    if user is None:
        return utils.error("No such user.")
    if not anubis.user.allow_view(user):
        return utils.error("You may not view the user's reviews.")

    reviewer_calls = [
        anubis.call.get_call(r.value)
        for r in flask.g.db.view(
            "calls", "reviewer", key=user["username"], reduce=False
        )
    ]
    # Reviews in only one call; redirect to its reviews page for the reviewer.
    if len(reviewer_calls) == 1:
        return flask.redirect(
            flask.url_for(
                "reviews.call_reviewer",
                cid=reviewer_calls[0]["identifier"],
                username=username,
            )
        )
    # Get the number of reviews on each call for the reviewer.
    for c in reviewer_calls:
        c["n_reviews"] = anubis.database.get_count(
            "reviews", "call_reviewer", [c["identifier"], user["username"]]
        )

    reviews = anubis.database.get_docs("reviews", "reviewer", user["username"])
    return flask.render_template(
        "reviews/reviewer.html",
        user=user,
        reviewer_calls=reviewer_calls,
        reviews=reviews,
    )


def get_reviews_xlsx(call, proposals, reviews_lookup):
    "Return the content for the XLSX file for the list of reviews."
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {"in_memory": True})
    formats = utils.create_xlsx_formats(wb)
    # Hard str(len) limit for worksheet title.
    ws = wb.add_worksheet(f"Reviews in call {call['identifier']}"[:31])
    ws.freeze_panes(1, 1)
    ws.set_row(0, 60, formats["head"])
    ws.set_column(0, 0, 16, formats["normal"])
    ws.set_column(1, 1, 40, formats["normal"])
    ws.set_column(2, 2, 20, formats["normal"])
    ws.set_column(3, 3, 40, formats["normal"])

    nrow = 0
    row = [
        "Proposal",
        "Proposal title",
        "Submitter",
        "Email",
        "Affiliation",
        "Reviewer",
        "Review",
        "Finalized",
    ]
    ncol = len(row)
    for field in call["review"]:
        row.append(field["title"] or field["identifier"].capitalize())
        if field["type"] in (constants.LINE, constants.EMAIL):
            ws.set_column(ncol, ncol, 40, formats["normal"])
        elif field["type"] == constants.TEXT:
            ws.set_column(ncol, ncol, 60, formats["normal"])
        ncol += 1
    ws.write_row(nrow, 0, row)
    nrow += 1

    for proposal in proposals:
        for reviewer in call["reviewers"]:
            review = reviews_lookup.get(
                "{} {}".format(proposal["identifier"], reviewer)
            )
            if not review:
                continue
            user = anubis.user.get_user(username=proposal["user"])
            ncol = 0
            ws.write_url(
                nrow,
                ncol,
                flask.url_for(
                    "proposal.display", pid=proposal["identifier"], _external=True
                ),
                string=proposal["identifier"],
            )
            ncol += 1
            ws.write_string(nrow, ncol, proposal.get("title") or "")
            ncol += 1
            ws.write_string(nrow, ncol, anubis.user.get_fullname(user))
            ncol += 1
            ws.write_string(nrow, ncol, user.get("email") or "")
            ncol += 1
            ws.write_string(nrow, ncol, user.get("affiliation") or "")
            ncol += 1
            ws.write_string(nrow, ncol, anubis.user.get_fullname(reviewer))
            ncol += 1
            ws.write_url(
                nrow,
                ncol,
                flask.url_for("review.display", iuid=review["_id"], _external=True),
                string="Link",
            )
            ncol += 1
            ws.write_string(nrow, ncol, review.get("finalized") and "Yes" or "No")
            ncol += 1

            for field in call["review"]:
                value = review["values"].get(field["identifier"])
                # Ugly, but necessary...
                if value is not None and field["type"] == constants.DOCUMENT:
                    value = flask.url_for(
                        "review.document",
                        iuid=review["_id"],
                        fid=field["identifier"],
                        _external=True,
                    )
                utils.write_xlsx_field(ws, nrow, ncol, value, field["type"], formats)
                ncol += 1
            nrow += 1

    wb.close()
    return output.getvalue()


def get_rank_error(call, username):
    """Return True if the reviews by the reviewer in the call has
    rank fields with non-consecutive values.
    """
    reviews = anubis.database.get_docs(
        "reviews", "call_reviewer", [call["identifier"], username]
    )
    reviews = [r for r in reviews if r.get("finalized")]
    reviews = [r for r in reviews if not r["values"].get("conflict_of_interest")]
    if not reviews:
        return False
    identifiers = [
        f["identifier"]
        for f in call["review"]
        if f.get("banner") and f["type"] == constants.RANK
    ]
    for identifier in identifiers:
        ranks = set()
        for review in reviews:
            ranks.add(review["values"].get(identifier))
        if ranks != set(range(1, max(ranks) + 1)):
            return True
    return False

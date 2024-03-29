"User creation, display, edit. Login/logout endpoints."

import datetime
import fnmatch
import http.client
import json
import re

import flask
import werkzeug.security

import anubis.call
import anubis.database
from anubis import constants
from anubis import utils
from anubis.saver import Saver


blueprint = flask.Blueprint("user", __name__)


@blueprint.route("/login", methods=["GET", "POST"])
def login():
    "Login to a user account."
    if utils.http_GET():
        try:
            flask.session["login_target_url"] = flask.request.args["login_target_url"]
        except KeyError:
            pass
        return flask.render_template("user/login.html")

    elif utils.http_POST():
        try:
            do_login(
                flask.request.form.get("username"), flask.request.form.get("password")
            )
        except ValueError:
            return utils.error(
                "Invalid username/email or password, or account disabled.",
                flask.url_for("user.login"),
            )
        try:
            url = flask.session.pop("login_target_url")
        except KeyError:
            url = flask.url_for("home")
        return flask.redirect(url)

    # HEAD request gets here: return No_Content
    return "", 204


@blueprint.route("/logout", methods=["POST"])
def logout():
    "Logout from the user account."
    flask.session.pop("username", None)
    return flask.redirect(flask.url_for("home"))


@blueprint.route("/register", methods=["GET", "POST"])
def register():
    "Register a new user account."
    if utils.http_GET():
        return flask.render_template(
            "user/register.html", data_policy=flask.g.db["data_policy"]
        )

    elif utils.http_POST():
        try:
            with UserSaver() as saver:
                saver.set_username(flask.request.form.get("username"))
                saver.set_email(flask.request.form.get("email") or "")
                saver.set_orcid(flask.request.form.get("orcid"))
                # Admin or staff may enable an account directly.
                if flask.g.am_admin or flask.g.am_staff:
                    if utils.to_bool(flask.request.form.get("enable")):
                        saver.set_status(constants.ENABLED)
                saver.set_role(constants.USER)
                saver.set_call_creator(False)
                saver.set_password()  # No argument; sets code.
                saver.set_givenname(flask.request.form.get("givenname"))
                saver.set_familyname(flask.request.form.get("familyname"))
                saver.set_gender(flask.request.form.get("gender"))
                saver.set_birthdate(flask.request.form.get("birthdate"))
                saver.set_degree(flask.request.form.get("degree"))
                saver.set_affiliation(
                    flask.request.form.get("affiliation")
                    or flask.request.form.get("affiliation_other")
                )
                saver.set_postaladdress(flask.request.form.get("postaladdress"))
                saver.set_phone(flask.request.form.get("phone"))
            user = saver.doc
        except ValueError as error:
            return utils.error(error, flask.url_for("user.register"))
        # Directly enabled due to white list; send code in email to the user.
        if user["status"] == constants.ENABLED:
            try:
                if utils.to_bool(flask.request.form.get("send_email")):
                    send_email_password_code(user, "registered")
                    utils.flash_message(
                        "User account created. An email with a link"
                        " to set the password has been sent."
                    )
                else:
                    utils.flash_message("User account created. No email was sent.")
            except ValueError:
                utils.flash_warning(
                    "User account created, but no email could be sent."
                    " The email server is badly configured."
                    " You must request the one-time password setting code"
                    " from the admin of the site."
                )
        # Was set to 'pending'; try to send email to admins.
        else:
            utils.flash_message(
                "User account created; an email will be sent"
                " when it has been enabled by the admin."
            )
            admins = get_users(role=constants.ADMIN, status=constants.ENABLED)
            recipients = [u["email"] for u in admins if u["email"]]
            site = flask.current_app.config["SITE_NAME"]
            title = f"{site} user account pending"
            url = flask.url_for(
                "user.display", username=user["username"], _external=True
            )
            text = f"To enable the user account, go to {url}\n\n" "/The Anubis system"
            try:
                utils.send_email(recipients, title, text)
            except ValueError:
                if flask.g.am_admin or flask.g.am_staff:
                    utils.flash_warning(
                        "No email sent; email server badly configured."
                        " The code must be sent manually to the user."
                    )
                    return flask.redirect(
                        flask.url_for("user.display", username=user["username"])
                    )
        if flask.g.am_admin:
            return flask.redirect(flask.url_for("user.all"))
        else:
            return flask.redirect(flask.url_for("home"))


@blueprint.route("/reset", methods=["GET", "POST"])
def reset():
    "Reset the password for a user account and send email."
    if utils.http_GET():
        email = flask.request.args.get("email") or ""
        email = email.lower()
        return flask.render_template("user/reset.html", email=email)

    elif utils.http_POST():
        try:
            user = get_user(email=flask.request.form["email"])
            if user is None:
                raise KeyError
            if user["status"] != constants.ENABLED:
                raise KeyError
        except KeyError:
            if flask.g.am_admin:
                utils.flash_error("No such user.")
            else:
                # Don't reveal whether the user exists or not.
                utils.flash_message(
                    "An email has been sent, if a user account"
                    " with the given email address exists."
                )
        else:
            with UserSaver(user) as saver:
                saver.set_password()
            try:
                send_email_password_code(user, "password reset")
                utils.flash_message(
                    "An email has been sent, if a user account"
                    " with the given email address exists."
                )
            except ValueError:
                if flask.g.am_admin:
                    utils.flash_warning(
                        "No automatic email can be sent. The one-time"
                        " code must be sent manually to the user."
                    )
                else:
                    utils.flash_warning(
                        "No automatic email can be sent."
                        " The one-time code for setting your password"
                        " must be obtained from the administrator."
                    )
        if flask.g.am_admin:
            return flask.redirect(
                flask.url_for("user.display", username=user["username"])
            )
        else:
            return flask.redirect(flask.url_for("home"))


@blueprint.route("/password", methods=["GET", "POST"])
def password():
    "Set the password for a user account, and login user."
    if utils.http_GET():
        return flask.render_template(
            "user/password.html",
            username=flask.request.args.get("username"),
            code=flask.request.args.get("code"),
        )

    elif utils.http_POST():
        try:
            username = flask.request.form.get("username") or ""
            if not username:
                raise ValueError("No such user or wrong code.")
            user = get_user(username=username)
            if user is None:
                raise ValueError("No such user or wrong code.")
            if flask.g.am_admin:
                code = ""
            else:
                code = flask.request.form.get("code") or ""
                if user["password"] != f"code:{code}":
                    raise ValueError("No such user or wrong code.")
            password = flask.request.form.get("password") or ""
            if len(password) < flask.current_app.config["MIN_PASSWORD_LENGTH"]:
                raise ValueError("Too short password.")
        except ValueError as error:
            return utils.error(
                error, flask.url_for("user.password", username=username, code=code)
            )
        with UserSaver(user) as saver:
            saver.set_password(password)
        utils.flash_message("Password set.")
        if flask.g.am_admin:
            return flask.redirect(flask.url_for("user.all"))
        else:
            do_login(username, password)
            return flask.redirect(flask.url_for("home"))


@blueprint.route("/display/<username>")
@utils.login_required
def display(username):
    "Display the given user."
    user = get_user(username=username)
    if user is None:
        return utils.error("No such user.")
    if not allow_view(user):
        return utils.error("Access to user display not allowed.")

    reviewer_calls = [
        anubis.call.get_call(r.value)
        for r in flask.g.db.view(
            "calls", "reviewer", key=user["username"], reduce=False
        )
    ]
    for call in reviewer_calls:
        call["n_reviews"] = anubis.database.get_count(
            "reviews", "call_reviewer", [call["identifier"], user["username"]]
        )
    user_proposals_count = anubis.database.get_count(
        "proposals", "user", user["username"]
    ) + anubis.database.get_count("proposals", "access", user["username"])
    # The user's own grants and the ones she has access to.
    user_grants_count = anubis.database.get_count(
        "grants", "user", user["username"]
    ) + anubis.database.get_count("grants", "access", user["username"])
    return flask.render_template(
        "user/display.html",
        user=user,
        reviewer_calls=reviewer_calls,
        review_rank_errors=set(
            [
                call["identifier"]
                for call in reviewer_calls
                if anubis.reviews.get_rank_error(call, username)
            ]
        ),
        allow_create_call=anubis.call.allow_create(user),
        user_calls_count=anubis.database.get_count("calls", "owner", user["username"]),
        user_proposals_count=user_proposals_count,
        user_reviews_count=anubis.database.get_count(
            "reviews", "reviewer", user["username"]
        ),
        user_grants_count=user_grants_count,
        allow_enable_disable=allow_enable_disable(user),
        allow_edit=allow_edit(user),
        allow_delete=allow_delete(user),
        data_policy=flask.g.db["data_policy"],
    )


@blueprint.route("/display/<username>/edit", methods=["GET", "POST", "DELETE"])
@utils.login_required
def edit(username):
    "Edit the user. Or delete the user."
    user = get_user(username=username)
    if user is None:
        return utils.error("No such user.")
    if not allow_edit(user):
        return utils.error("Access to user edit not allowed.")

    if utils.http_GET():
        return flask.render_template(
            "user/edit.html", user=user, am_admin_not_self=am_admin_not_self(user)
        )

    elif utils.http_POST():
        try:
            with UserSaver(user) as saver:
                if flask.g.am_admin:
                    email = flask.request.form.get("email")
                    saver.set_email(email)
                saver.set_orcid(flask.request.form.get("orcid"))
                if am_admin_not_self(user):
                    saver.set_role(flask.request.form.get("role"))
                    saver.set_call_creator(
                        utils.to_bool(flask.request.form.get("call_creator"))
                    )
                saver.set_givenname(flask.request.form.get("givenname"))
                saver.set_familyname(flask.request.form.get("familyname"))
                saver.set_gender(flask.request.form.get("gender"))
                saver.set_birthdate(flask.request.form.get("birthdate"))
                saver.set_degree(flask.request.form.get("degree"))
                saver.set_affiliation(
                    flask.request.form.get("affiliation")
                    or flask.request.form.get("affiliation_other")
                )
                saver.set_postaladdress(flask.request.form.get("postaladdress"))
                saver.set_phone(flask.request.form.get("phone"))
        except ValueError as error:
            utils.flash_error(error)
        return flask.redirect(flask.url_for("user.display", username=user["username"]))

    elif utils.http_DELETE():
        if not allow_delete(user):
            return utils.error(
                "Cannot delete the user account; is admin or owns entities.",
                flask.url_for("user.display", username=username),
            )
        anubis.database.delete(user)
        utils.flash_message(f"Deleted user {username}.")
        if flask.g.am_admin:
            return flask.redirect(flask.url_for("user.all"))
        else:
            return flask.redirect(flask.url_for("home"))


@blueprint.route("/logs/<username>")
@utils.login_required
def logs(username):
    "Display the log records for the given user account."
    user = get_user(username=username)
    if user is None:
        return utils.error("No such user.")
    if not allow_view(user):
        return utils.error("Access to user logs not allowed.")

    return flask.render_template(
        "logs.html",
        title=f"User {user['username']}",
        back_url=flask.url_for("user.display", username=user["username"]),
        logs=anubis.database.get_logs(user["_id"]),
    )


@blueprint.route("/all")
@utils.staff_required
def all():
    "Display list of all user accounts."
    users = get_users()
    # A single call using group_level 1 is much more efficient
    # than calling once for each user.
    result = flask.g.db.view("proposals", "user", group_level=1, reduce=True)
    proposals_counts = dict([(r.key, r.value) for r in result])
    result = flask.g.db.view("reviews", "reviewer", group_level=1, reduce=True)
    reviews_counts = dict([(r.key, r.value) for r in result])
    # User's own grants.
    result = flask.g.db.view("grants", "user", group_level=1, reduce=True)
    grants_counts = dict([(r.key, r.value) for r in result])
    # Grants user has access to.
    result = flask.g.db.view("grants", "access", group_level=1, reduce=True)
    for row in result:
        try:
            grants_counts[row.key] += row.value
        except KeyError:
            grants_counts[row.key] = row.value
    for user in users:
        username = user["username"]
        user["all_proposals_count"] = proposals_counts.get(username)
        user["all_reviews_count"] = reviews_counts.get(username)
        user["all_grants_count"] = grants_counts.get(username)
    return flask.render_template("user/all.html", users=users)


@blueprint.route("/pending")
@utils.staff_required
def pending():
    "Display list of all pending user accounts."
    users = get_users(status=constants.PENDING)
    return flask.render_template("user/pending.html", users=users)


@blueprint.route("/staff")
@utils.staff_required
def staff():
    "Display list of all admin and staff user accounts."
    users = get_users(role=constants.ADMIN) + get_users(role=constants.STAFF)
    return flask.render_template("user/staff.html", users=users)


@blueprint.route("/enable/<username>", methods=["POST"])
@utils.staff_required
def enable(username):
    "Enable the given user account."
    user = get_user(username=username)
    if user is None:
        return utils.error("No such user.")
    if not allow_enable_disable(user):
        return utils.error("You may not enable the user account.")

    with UserSaver(user) as saver:
        saver.set_status(constants.ENABLED)
        saver.set_password()
    try:
        send_email_password_code(user, "enabled")
    except ValueError:
        utils.flash_warning("User account enabled. No email could be sent.")
    else:
        utils.flash_message("User account enabled; email sent.")
    return flask.redirect(flask.url_for("user.display", username=username))


@blueprint.route("/disable/<username>", methods=["POST"])
@utils.staff_required
def disable(username):
    "Disable the given user account."
    user = get_user(username=username)
    if user is None:
        return utils.error("No such user.")
    if not allow_enable_disable(user):
        return utils.error("You may not disable the user account.")

    with UserSaver(user) as saver:
        saver.set_status(constants.DISABLED)
    return flask.redirect(flask.url_for("user.display", username=username))


class UserSaver(Saver):
    "User account saver context manager."

    DOCTYPE = constants.USER
    HIDDEN_FIELDS = ["password"]

    def initialize(self):
        "Set the status for a new user."
        self.doc["status"] = constants.PENDING

    def finish(self):
        "Check that required fields have been set."
        for key in ["username", "role", "status"]:
            if not self.doc.get(key):
                raise ValueError(f"invalid user: {key} not set")

    def set_username(self, username):
        if "username" in self.doc:
            raise ValueError("username cannot be changed")
        if not constants.ID_RX.match(username):
            raise ValueError("invalid username; must be an identifier")
        if get_user(username=username):
            raise ValueError("username already in use")
        self.doc["username"] = username

    def set_email(self, email):
        """Set the email address of the account.
        Enable if in whitelist.
        Admin may set the email to None.
        """
        email = email.strip().lower()
        if not email:
            if flask.g.am_admin:
                self.doc["email"] = None
                return
            else:
                raise ValueError("No email address provided.")
        if email == self.doc.get("email"):
            return
        if not constants.EMAIL_RX.match(email):
            raise ValueError("invalid email")
        if get_user(email=email):
            raise ValueError("email already in use")
        self.doc["email"] = email
        if self.doc.get("status") == constants.PENDING:
            # Filename pattern matching instead of regexp; easier to specify.
            # The whitelist has not been fetched if the CLI is calling this.
            for p in flask.current_app.config.get("USER_ENABLE_EMAIL_WHITELIST", []):
                if fnmatch.fnmatch(email, p):
                    self.set_status(constants.ENABLED)
                    break

    def set_orcid(self, orcid):
        "Set the ORCID of the account."
        if orcid:
            if len(orcid) == 16:  # Add in dashes.
                orcid = f"{orcid[0:4]}-{orcid[4:8]}-{orcid[8:12]}-{orcid[12:16]}"
            if len(orcid) != 19 or not (
                orcid[4] == "-" and orcid[9] == "-" and orcid[14] == "-"
            ):
                raise ValueError(
                    "Invalid ORCID format; length is wrong, or dashes in the wrong places."
                )
            # Compute checksum according to https://support.orcid.org/hc/en-us/articles/360006897674-Structure-of-the-ORCID-Identifier
            total = 0
            for c in orcid[:-1]:
                if c == "-":
                    continue
                digit = int(c)
                total = (total + digit) * 2
            remainder = total % 11
            result = (12 - remainder) % 11
            if not ((result == 10 and orcid[-1] == "X") or (result == int(orcid[-1]))):
                raise ValueError("Invalid ORCID; checksum is wrong.")
            view = flask.g.db.view("users", "orcid", key=orcid)
            if set([r.value for r in view]).difference([self.doc["username"]]):
                raise ValueError("ORCID is already in use for another user account.")
        self.doc["orcid"] = orcid or None

    def set_status(self, status):
        if status not in constants.USER_STATUSES:
            raise ValueError("invalid status")
        self.doc["status"] = status

    def set_role(self, role):
        if role not in constants.USER_ROLES:
            raise ValueError("invalid role")
        self.doc["role"] = role

    def set_call_creator(self, yes):
        self.doc["call_creator"] = bool(yes)

    def set_givenname(self, givenname):
        self.doc["givenname"] = givenname or None

    def set_familyname(self, familyname):
        self.doc["familyname"] = familyname or None

    def set_gender(self, gender):
        if gender not in flask.current_app.config["USER_GENDERS"]:
            gender = None
        self.doc["gender"] = gender

    def set_birthdate(self, birthdate):
        if not flask.current_app.config["USER_BIRTHDATE"]:
            return
        if birthdate:
            try:
                datetime.datetime.strptime(birthdate, "%Y-%m-%d")
            except ValueError:
                birthdate = None
        self.doc["birthdate"] = birthdate

    def set_degree(self, degree):
        if degree not in flask.current_app.config["USER_DEGREES"]:
            degree = None
        self.doc["degree"] = degree

    def set_affiliation(self, affiliation):
        self.doc["affiliation"] = (
            flask.current_app.config["USER_AFFILIATION"] and affiliation or None
        )

    def set_postaladdress(self, postaladdress):
        self.doc["postaladdress"] = (
            flask.current_app.config["USER_POSTALADDRESS"] and postaladdress or None
        )

    def set_phone(self, phone):
        self.doc["phone"] = flask.current_app.config["USER_PHONE"] and phone or None

    def set_password(self, password=None):
        "Set the password; a one-time code if no password provided."
        if password:
            if len(password) < flask.current_app.config["MIN_PASSWORD_LENGTH"]:
                raise ValueError("password too short")
            self.doc["password"] = werkzeug.security.generate_password_hash(password)
        else:
            self.doc["password"] = f"code:{utils.get_iuid()}"

    def set_last_login(self):
        self.doc["last_login"] = utils.get_now()


def get_user(username=None, email=None, orcid=None):
    """Return the user for the given username, email or ORCID.
    Return None if no such user.
    """
    if username:
        username = username.strip()
    if email:
        email = email.strip()
    if username:
        try:
            return utils.cache_get(f"username {username}")
        except KeyError:
            docs = anubis.database.get_docs("users", "username", username)
            if len(docs) == 1:
                return docs[0]
            else:
                return None
    elif email:
        email = email.lower()
        try:
            return utils.cache_get(f"email {email}")
        except KeyError:
            docs = anubis.database.get_docs("users", "email", email)
            if len(docs) == 1:
                return docs[0]
            else:
                return None
    elif orcid:
        try:
            return utils.cache_get(f"orcid {orcid}")
        except KeyError:
            docs = anubis.database.get_docs("users", "orcid", orcid)
            if len(docs) == 1:
                return docs[0]
            else:
                return None
    else:
        return None


def get_users(role=None, status=None):
    "Return the users specified by role and optionally by status."
    assert role is None or role in constants.USER_ROLES
    assert status is None or status in constants.USER_STATUSES
    if role is None:
        if status is None:
            result = [
                r.doc for r in flask.g.db.view("users", "role", include_docs=True)
            ]
        else:
            result = [
                r.doc
                for r in flask.g.db.view(
                    "users", "status", key=status, include_docs=True
                )
            ]
    else:
        result = [
            r.doc for r in flask.g.db.view("users", "role", key=role, include_docs=True)
        ]
        if status is not None:
            result = [d for d in result if d["status"] == status]
    return result


def get_current_user():
    """Return the user for the current session.
    Return None if no such user, or disabled.
    """
    user = get_user(username=flask.session.get("username"))
    if user is None or user["status"] != constants.ENABLED:
        flask.session.pop("username", None)
        return None
    return user


def get_fullname(user):
    "Return full name of user, or family name, or user name."
    if isinstance(user, str):
        user = get_user(username=user)
        if user is None:
            return "?"
    if user.get("familyname"):
        name = user["familyname"]
        if user.get("givenname"):
            return f"{user['givenname']} {name}"
        return name
    return user["username"]


def do_login(username, password):
    """Set the session cookie if successful login.
    The username can also be the email address or the ORCID of the user account.
    Raise ValueError if some problem.
    """
    if not username:
        raise ValueError
    if not password:
        raise ValueError
    user = get_user(username=username)
    if not user:
        user = get_user(email=username)
        if not user:
            user = get_user(orcid=username)
            if not user:
                raise ValueError
    if not werkzeug.security.check_password_hash(user["password"], password):
        raise ValueError
    if user["status"] != constants.ENABLED:
        raise ValueError
    with UserSaver(user) as saver:
        saver.set_last_login()
    flask.session["username"] = user["username"]
    flask.session.permanent = True


def send_email_password_code(user, action):
    """Send an email with the one-time code to the user's email address.
    No action if no email address for user.
    Raise ValueError if email server not configured.
    Raise KeyError if email could not be sent; server misconfigured.
    """
    if not user["email"]:
        return
    site = flask.current_app.config["SITE_NAME"]
    title = f"{site} user account {action}"
    url = flask.url_for(
        ".password",
        username=user["username"],
        code=user["password"][len("code:") :],
        _external=True,
    )
    if action == "registration":
        action = "has been created"
    elif action == "password reset":
        action = "has had its password reset"
    elif action == "enabled":
        action = "has been enabled"
    text = (
        f"Your account {user['username']} in the {site} system"
        f" {action}.\n\nTo set your password, go to {url}\n\n"
        "/The Anubis system"
    )
    utils.send_email(user["email"], title, text)


def am_admin(user=None):
    "Is the user admin? Default user: current_user."
    if user is None:
        user = flask.g.current_user
    if user is None:
        return False
    return user["role"] == constants.ADMIN


def am_staff(user=None):
    "Is the user staff? Default user: current_user."
    if user is None:
        user = flask.g.current_user
    if user is None:
        return False
    return user["role"] == constants.STAFF


def allow_view(user):
    """Is the current user allowed to view the user account?
    Yes, if current user is admin, staff or self.
    """
    if not flask.g.current_user:
        return False
    if flask.g.am_admin:
        return True
    if flask.g.am_staff:
        return True
    if flask.g.current_user["username"] == user["username"]:
        return True
    return False


def allow_edit(user):
    """Is the current user allowed to edit the user account?
    Yes, if current user is admin or staff or self.
    """
    if not flask.g.current_user:
        return False
    if flask.g.am_admin:
        return True
    if flask.g.am_staff:
        return True
    if flask.g.current_user["username"] == user["username"]:
        return True
    return False


def allow_delete(user):
    """Can the the given user account be deleted?
    Only when user is not admin, and has no proposals and no reviews,
    and is not reviewer in any call.
    Note that the user herself may be able to delete the account.
    """
    if user["role"] == constants.ADMIN:
        return False
    if anubis.database.get_count("proposals", "user", user["username"]):
        return False
    if anubis.database.get_count("reviews", "reviewer", user["username"]):
        return False
    if anubis.database.get_count("calls", "reviewer", user["username"]):
        return False
    return True


def allow_enable_disable(user):
    """Is the current user allowed to enable or disable the user account?
    Yes, if current user is admin or staff and not self.
    """
    if (
        flask.g.am_admin
        or flask.g.am_staff
        and flask.g.current_user["username"] != user["username"]
    ):
        return True
    return False


def am_admin_not_self(user):
    "Is the current user admin and not self?"
    if flask.g.am_admin and flask.g.current_user["username"] != user["username"]:
        return True
    return False

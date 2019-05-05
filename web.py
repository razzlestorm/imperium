import os
import requests
from datetime import timedelta
from flask import Flask, render_template, jsonify, abort, session, redirect, request, url_for
from flask_migrate import Migrate
from misc.helpers import CardHelper
from models.base_model import db
from models.data_models import Coach, Card, Account, Transaction, Tournament, TournamentSignups, Duster, TransactionError
from services import PackService, TournamentService, RegistrationError, WebHook, DusterService, DustingError, NotificationService
from models.marsh_models import ma, coach_schema, cards_schema, coaches_schema, tournaments_schema, tournament_schema, duster_schema
from sqlalchemy.orm import raiseload
from requests_oauthlib import OAuth2Session

os.environ["YOURAPPLICATION_SETTINGS"] = "config/config.py"

def create_app():
    app = Flask(__name__)
    app.config["DEBUG"] = True
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config.from_envvar('YOURAPPLICATION_SETTINGS')
    db.init_app(app)
    ma.init_app(app)
    
    # register wehook as Tournament service notifier
    NotificationService.register_notifier(WebHook(app.config['DISCORD_WEBHOOK_BANK']).send)
    return app

app = create_app()
migrate = Migrate(app, db)

API_BASE_URL = os.environ.get('API_BASE_URL', 'https://discordapp.com/api')
AUTHORIZATION_BASE_URL = API_BASE_URL + '/oauth2/authorize'
TOKEN_URL = API_BASE_URL + '/oauth2/token'

if 'http://' in app.config["OAUTH2_REDIRECT_URI"]:
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = 'true'

def token_updater(token):
    session['oauth2_token'] = token

def make_session(token=None, state=None, scope=None):
    return OAuth2Session(
        client_id=app.config["OAUTH2_CLIENT_ID"],
        token=token,
        state=state,
        scope=scope,
        redirect_uri=app.config["OAUTH2_REDIRECT_URI"],
        auto_refresh_kwargs={
            'client_id': app.config["OAUTH2_CLIENT_ID"],
            'client_secret': app.config["SECRET_KEY"],
        },
        auto_refresh_url=TOKEN_URL,
        token_updater=token_updater)

def current_user():
    return session['discord_user'] if 'discord_user' in session else None

class InvalidUsage(Exception):
    status_code = 400

    def __init__(self, message, status_code=None, payload=None):
        Exception.__init__(self)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        rv = dict(self.payload or ())
        rv['message'] = self.message
        return rv

@app.errorhandler(InvalidUsage)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response

@app.route('/signin')
def signin():
    scope = request.args.get(
        'scope',
        'identify')
    discord = make_session(scope=scope.split(' '))
    authorization_url, state = discord.authorization_url(AUTHORIZATION_BASE_URL)
    session['oauth2_state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def callback():
    if request.values.get('error'):
        return request.values['error']
    discord = make_session(state=session.get('oauth2_state'))
    token = discord.fetch_token(
        TOKEN_URL,
        client_secret=app.config["SECRET_KEY"],
        authorization_response=request.url)
    session['oauth2_token'] = token
    discord = make_session(token=session.get('oauth2_token'))
    user = discord.get(API_BASE_URL + '/users/@me').json()
    # store it in session
    session['discord_user'] = user
    return redirect(url_for('.index'))

@app.before_request
def before_request():
    session.permanent = True
    app.permanent_session_lifetime = timedelta(days=30)

@app.route('/me')
def me():
    return jsonify(user=session.get('discord_user',{'code':0}))

@app.route("/")
def index():
    starter_cards = PackService.generate("starter").cards
    return render_template("index.html",starter_cards=starter_cards)

@app.route("/coaches", methods=["GET"])
def get_coaches():
    all_coaches = Coach.query.options(raiseload(Coach.cards),raiseload(Coach.packs)).all()
    result = coaches_schema.dump(all_coaches)
    return jsonify(result.data)

@app.route("/tournaments", methods=["GET"])
def get_tournaments():
    all_tournaments = Tournament.query.options(raiseload(Tournament.coaches)).filter(Tournament.status.in_(("OPEN","RUNNING"))).all()
    result = tournaments_schema.dump(all_tournaments)
    return jsonify(result.data)

@app.route("/tournaments/<int:tournament_id>/sign", methods=["GET"])
def tournament_sign(tournament_id):
    if current_user():
        try:
            tourn = Tournament.query.get(tournament_id)
            coach = Coach.query.filter_by(disc_id=current_user()['id']).one_or_none()
            if not coach:
                raise InvalidUsage("Coach not found", status_code=403)    
            TournamentService.register(tourn,coach)
            result = tournament_schema.dump(tourn)
            return jsonify(result.data)
        except RegistrationError as e:
            raise InvalidUsage(str(e), status_code=403)
    else:
        raise InvalidUsage('You are not authenticated', status_code=401)

@app.route("/tournaments/<int:tournament_id>/resign", methods=["GET"])
def tournament_resign(tournament_id):
    if current_user():
        try:
            tourn = Tournament.query.get(tournament_id)
            coach = Coach.query.filter_by(disc_id=current_user()['id']).one_or_none()
            if not coach:
                raise InvalidUsage("Coach not found", status_code=403)    
            TournamentService.unregister(tourn,coach)
            result = tournament_schema.dump(tourn)
            return jsonify(result.data)
        except RegistrationError as e:
            raise InvalidUsage(str(e), status_code=403)
    else:
        raise InvalidUsage('You are not authenticated', status_code=401)


def dust_template(mode="add",card_id=None):
    if current_user():
        try:
            coach = Coach.query.filter_by(disc_id=current_user()['id']).one_or_none()
            if not coach:
                raise InvalidUsage("Coach not found", status_code=403)
            if mode=="add":
                DusterService.dust_card_by_id(coach,card_id)
            elif mode=="remove":
                DusterService.undust_card_by_id(coach,card_id)
            elif mode=="cancel":
               DusterService.cancel_duster(coach)
            elif mode=="commit":
                DusterService.commit_duster(coach)
            result = duster_schema.dump(coach.duster)
            return jsonify(result.data)
        except (DustingError,TransactionError) as e:
            raise InvalidUsage(str(e), status_code=403)
    else:
        raise InvalidUsage('You are not authenticated', status_code=401)

@app.route("/duster/cancel", methods=["GET"])
def dust_cancel():
    return dust_template("cancel")

@app.route("/duster/commit", methods=["GET"])
def dust_commit():
    return dust_template("commit")

@app.route("/duster/add/<int:card_id>", methods=["GET"])
def card_dust_add(card_id):
    return dust_template("add",card_id)

@app.route("/duster/remove/<int:card_id>", methods=["GET"])
def card_dust_remove(card_id):
    return dust_template("remove",card_id)

@app.route("/coaches/<int:coach_id>", methods=["GET"])
def get_coach(coach_id):
    coach = Coach.query.get(coach_id)
    if coach is None:
        abort(404)
    result = coach_schema.dump(coach)
    return jsonify(result.data)

@app.route("/cards/starter", methods=["GET"])
def get_starter_cards():
    starter_cards = PackService.generate("starter").cards
    result = cards_schema.dump(starter_cards)
    return jsonify(result.data)

# run the application
if __name__ == "__main__":
    app.run()

import six

from datetime import datetime, timedelta
import csv

from sqlalchemy.exc import IntegrityError

from flask import Response, current_app, jsonify, make_response, redirect, request

from baseframe import _
from coaster.auth import current_auth
from coaster.utils import getbool, make_name, midnight_to_utc, utcnow
from coaster.views import ClassView, render_with, requestargs, route

from .. import app, funnelapp
from ..models import ContactExchange, Project, TicketParticipant, db
from ..utils import abort_null, format_twitter_handle
from .helpers import app_url_for
from .login_session import requires_login


def contact_details(ticket_participant):
    if ticket_participant:
        return {
            'fullname': ticket_participant.fullname,
            'company': ticket_participant.company,
            'email': ticket_participant.email,
            'twitter': format_twitter_handle(ticket_participant.twitter),
            'phone': ticket_participant.phone,
        }


@route('/account/contacts')
class ContactView(ClassView):
    current_section = 'account'

    def get_project(self, uuid_b58):
        return (
            Project.query.filter_by(uuid_b58=uuid_b58)
            .options(db.load_only(Project.id, Project.uuid, Project.title))
            .one_or_404()
        )

    @route('', endpoint='contacts')
    @requires_login
    @render_with('contacts.html.jinja2')
    def contacts(self):
        """Grouped list of contacts"""
        archived = getbool(request.args.get('archived'))
        return {
            'contacts': ContactExchange.grouped_counts_for(
                current_auth.user, archived=archived
            )
        }

    def contacts_to_csv(self, contacts, timezone, filename):
        """
        Returns a CSV of given contacts
        """
        outfile = six.StringIO()
        out = csv.writer(outfile)
        out.writerow(
            [
                'scanned_at',
                'fullname',
                'email',
                'phone',
                'twitter',
                'job_title',
                'company',
                'city',
            ]
        )
        for contact in contacts:
            proxy = contact.current_access()
            ticket_participant = proxy.ticket_participant
            out.writerow(
                [
                    proxy.scanned_at.astimezone(timezone)
                    .replace(second=0, microsecond=0, tzinfo=None)
                    .isoformat(),  # Strip precision from timestamp
                    ticket_participant.fullname,
                    ticket_participant.email,
                    ticket_participant.phone,
                    ticket_participant.twitter,
                    ticket_participant.job_title,
                    ticket_participant.company,
                    ticket_participant.city,
                ]
            )

        outfile.seek(0)
        return Response(
            outfile.getvalue(),
            content_type='text/csv',
            headers=[
                (
                    'Content-Disposition',
                    'attachment;filename="{filename}.csv"'.format(filename=filename),
                )
            ],
        )

    @route('<uuid_b58>/<datestr>.csv', endpoint='contacts_project_date_csv')
    @requires_login
    def project_date_csv(self, uuid_b58, datestr):
        """Contacts for a given project and date in CSV format"""
        archived = getbool(request.args.get('archived'))
        project = self.get_project(uuid_b58)
        date = datetime.strptime(datestr, '%Y-%m-%d').date()

        contacts = ContactExchange.contacts_for_project_and_date(
            current_auth.user, project, date, archived
        )
        return self.contacts_to_csv(
            contacts,
            timezone=project.timezone,
            filename='contacts-{project}-{date}'.format(
                project=make_name(project.title), date=date.strftime('%Y%m%d')
            ),
        )

    @route('<uuid_b58>.csv', endpoint='contacts_project_csv')
    @requires_login
    def project_csv(self, uuid_b58):
        """Contacts for a given project in CSV format"""
        archived = getbool(request.args.get('archived'))
        project = self.get_project(uuid_b58)

        contacts = ContactExchange.contacts_for_project(
            current_auth.user, project, archived
        )
        return self.contacts_to_csv(
            contacts,
            timezone=project.timezone,
            filename='contacts-{project}'.format(project=make_name(project.title)),
        )

    @route('scan', endpoint='scan_contact')
    @requires_login
    @render_with('scan_contact.html.jinja2')
    def scan(self):
        """Scan a badge"""
        return {}

    @route('scan/connect', endpoint='scan_connect', methods=['POST'])
    @requires_login
    @requestargs(('puk', abort_null), ('key', abort_null))
    def connect(self, puk, key):
        """Scan verification"""
        ticket_participant = TicketParticipant.query.filter_by(puk=puk, key=key).first()
        if not ticket_participant:
            return make_response(
                jsonify(status='error', message="Attendee details not found"), 404
            )
        project = ticket_participant.project
        if project.schedule_end_at:
            if (
                midnight_to_utc(
                    project.schedule_end_at + timedelta(days=1), project.timezone
                )
                < utcnow()
            ):
                return make_response(
                    jsonify(status='error', message=_("This project has concluded")),
                    403,
                )

            try:
                contact_exchange = ContactExchange(
                    user=current_auth.actor, ticket_participant=ticket_participant
                )
                db.session.add(contact_exchange)
                db.session.commit()
            except IntegrityError:
                current_app.logger.warning("Contact already scanned")
                db.session.rollback()
            return jsonify(contact=contact_details(ticket_participant))
        else:
            return make_response(
                jsonify(status='error', message="Unauthorized contact exchange"), 403
            )


@route('/account/contacts')
class FunnelContactView(ClassView):
    @route('', endpoint='contacts')
    def contacts(self):
        return redirect(app_url_for(app, 'contacts', _external=True))

    @route('', endpoint='scan_contact')
    def scan(self):
        return redirect(app_url_for(app, 'scan_contact', _external=True))


ContactView.init_app(app)
FunnelContactView.init_app(funnelapp)

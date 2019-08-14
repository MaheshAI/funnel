# -*- coding: utf-8 -*-

from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.ext.hybrid import hybrid_property

from coaster.sqlalchemy import immutable

from . import BaseMixin, UuidMixin, db
from .profile import Profile
from .project import Project
from .user import User

__all__ = ['ProfileAdminMembership', 'ProjectCrewMembership']


class ImmutableMembershipMixin(UuidMixin, BaseMixin):
    """
    Support class for immutable memberships
    """

    __uuid_primary_key__ = True
    #: List of columns that will be copied into a new row when a membership is amended
    __role_columns__ = ()
    #: Parent column ('profile_id' or 'project_id' in the subclasses)
    __parent_column__ = None
    #: Require timezones in timestamps
    __with_timezone__ = True

    #: Start time of membership, ordinarily a mirror of created_at except
    #: for records created when the member table was added to the database
    granted_at = immutable(
        db.Column(db.TIMESTAMP(timezone=True), nullable=False, default=db.func.utcnow())
    )
    #: End time of membership, ordinarily a mirror of updated_at
    revoked_at = db.Column(db.TIMESTAMP(timezone=True), nullable=True)

    @declared_attr
    def revoked_by_id(cls):
        """Id of user who revoked the membership"""
        return db.Column(
            None, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True
        )

    @declared_attr
    def revoked_by(cls):
        """User who revoked the membership"""
        return db.relationship(User, foreign_keys=[cls.revoked_by_id])

    @hybrid_property
    def active(self):
        return self.revoked_at is None

    @active.expression
    def active(cls):  # NOQA: N805
        return cls.revoked_at.is_(None)

    @declared_attr
    def __table_args__(cls):
        return (
            db.Index(
                cls.__tablename__ + '_active',
                cls.__parent_column__,
                'user_id',
                unique=True,
                postgresql_where=db.text('revoked_at IS NULL'),
            ),
        )

    def offered_roles(self):
        """Roles offered by this membership record"""
        return set()

    # Subclasses must gate these methods in __roles__

    def revoke(self, actor):
        self.revoked_at = db.func.utcnow()
        self.revoked_by = actor

    def replace(self, actor, **roles):
        if not set(roles.keys()).issubset(self.__role_columns__):
            raise AttributeError("Unknown role")
        self.revoked_at = db.func.utcnow()
        self.revoked_by = actor
        new = type(self)(user=self.user)
        setattr(new, self.__parent_column__, getattr(self, self.__parent_column__))
        for column in self.__role_columns__:
            if column in roles:
                setattr(new, column, roles[column])
            else:
                setattr(new, column, getattr(self, column))
        db.session.add(new)
        return new


class ProfileAdminMembership(ImmutableMembershipMixin, db.Model):
    """
    A users can be an administrator of a profile and optionally an owner.
    Owners can manage other administrators.
    """

    __tablename__ = 'profile_admin_membership'

    # List of role columns in this model
    __role_columns__ = ('is_owner',)
    __parent_column__ = 'profile_id'

    # Control access to revocation and replacement methods
    __roles__ = {'profile_owner': {'call': {'revoke', 'replace'}}}

    #: Profile that this membership is being granted on
    profile_id = immutable(
        db.Column(None, db.ForeignKey('profile.id', ondelete='CASCADE'), nullable=False)
    )
    profile = immutable(
        db.relationship(
            Profile,
            backref=db.backref(
                'admin_memberships',
                lazy='dynamic',
                cascade='all, delete-orphan',
                passive_deletes=True,
            ),
        )
    )
    parent = immutable(db.synonym('profile'))

    #: User who is an admin or owner
    user_id = immutable(
        db.Column(
            None,
            db.ForeignKey('user.id', ondelete='CASCADE'),
            nullable=False,
            index=True,
        )
    )
    user = immutable(
        db.relationship(
            User,
            foreign_keys=[user_id],
            backref=db.backref(
                'profile_admin_memberships',
                lazy='dynamic',
                cascade='all, delete-orphan',
                passive_deletes=True,
            ),
        )
    )

    # Profile roles:
    is_owner = immutable(db.Column(db.Boolean, nullable=False, default=False))

    def offered_roles(self):
        """Roles offered by this membership record"""
        roles = {'profile_admin'}
        if self.is_owner:
            roles.add('profile_owner')
        return roles

    def roles_for(self, actor, anchors=()):
        """Roles available to the specified actor and anchors"""
        roles = super(ProfileAdminMembership, self).roles_for(actor, anchors)
        roles.update(self.parent.roles_for(actor, anchors))
        return roles


# Add active membership relationships to Profile and User

Profile.active_admin_memberships = db.relationship(
    ProfileAdminMembership,
    lazy='dynamic',
    primaryjoin=db.and_(
        ProfileAdminMembership.profile_id == Profile.id, ProfileAdminMembership.active
    ),
)

Profile.active_owner_memberships = db.relationship(
    ProfileAdminMembership,
    lazy='dynamic',
    primaryjoin=db.and_(
        ProfileAdminMembership.profile_id == Profile.id,
        ProfileAdminMembership.active,
        ProfileAdminMembership.is_owner.is_(True),
    ),
)

User.active_profile_admin_memberships = db.relationship(
    ProfileAdminMembership,
    lazy='dynamic',
    primaryjoin=db.and_(
        ProfileAdminMembership.user_id == User.id, ProfileAdminMembership.active
    ),
)

User.active_profile_owner_memberships = db.relationship(
    ProfileAdminMembership,
    lazy='dynamic',
    primaryjoin=db.and_(
        ProfileAdminMembership.user_id == User.id,
        ProfileAdminMembership.active,
        ProfileAdminMembership.is_owner.is_(True),
    ),
)

User.profiles_owned = association_proxy('active_profile_owner_memberships', 'profile')


class ProjectCrewMembership(ImmutableMembershipMixin, db.Model):
    """
    Users can be crew members of projects, with specified access rights.
    """

    __tablename__ = 'project_crew_membership'

    # List of is_role columns in this model
    __role_columns__ = ('is_editor', 'is_concierge', 'is_usher')
    __parent_column__ = 'project_id'

    project_id = immutable(
        db.Column(None, db.ForeignKey('project.id', ondelete='CASCADE'), nullable=False)
    )
    project = immutable(
        db.relationship(
            Project,
            backref=db.backref(
                'crew_memberships',
                lazy='dynamic',
                cascade='all, delete-orphan',
                passive_deletes=True,
            ),
        )
    )
    parent = immutable(db.synonym('project'))

    user_id = immutable(
        db.Column(
            None,
            db.ForeignKey('user.id', ondelete='CASCADE'),
            nullable=False,
            index=True,
        )
    )
    user = immutable(
        db.relationship(
            User,
            foreign_keys=[user_id],
            backref=db.backref(
                'profile_crew_memberships',
                lazy='dynamic',
                cascade='all, delete-orphan',
                passive_deletes=True,
            ),
        )
    )

    # Project crew roles (at least one must be True):

    #: Editors can edit all common and editorial details of an event
    is_editor = db.Column(db.Boolean, nullable=False, default=False)
    #: Concierges are responsible for logistics and have write access
    #: to common details plus read access to everything else. Unlike
    #: editors, they cannot edit the schedule
    is_concierge = db.Column(db.Boolean, nullable=False, default=False)
    #: Ushers help participants find their way around an event and have
    #: the ability to scan badges at the door
    is_usher = db.Column(db.Boolean, nullable=False, default=False)

    @declared_attr
    def __table_args__(cls):
        args = list(super(cls, cls).__table_args__)
        args.append(
            db.CheckConstraint(
                'is_editor IS TRUE OR is_concierge IS TRUE OR is_usher IS TRUE',
                name='project_crew_membership_has_role',
            )
        )
        return tuple(args)

    def offered_roles(self):
        """Roles offered by this membership record"""
        roles = set()
        if self.is_editor:
            roles.add('project_editor')
        if self.is_concierge:
            roles.add('project_concierge')
        if self.is_usher:
            roles.add('project_usher')
        return roles

    def roles_for(self, actor, anchors=()):
        """Roles available to the specified actor and anchors"""
        roles = super(ProjectCrewMembership, self).roles_for(actor, anchors)
        roles.update(self.parent.roles_for(actor, anchors))
        return roles


# Project relationships: all crew, vs specific roles

Project.active_crew_memberships = db.relationship(
    ProjectCrewMembership,
    lazy='dynamic',
    primaryjoin=db.and_(
        ProjectCrewMembership.project_id == Project.id, ProjectCrewMembership.active
    ),
)

Project.active_editor_memberships = db.relationship(
    ProjectCrewMembership,
    lazy='dynamic',
    primaryjoin=db.and_(
        ProjectCrewMembership.project_id == Project.id,
        ProjectCrewMembership.active,
        ProjectCrewMembership.is_editor.is_(True),
    ),
)

Project.active_concierge_memberships = db.relationship(
    ProjectCrewMembership,
    lazy='dynamic',
    primaryjoin=db.and_(
        ProjectCrewMembership.project_id == Project.id,
        ProjectCrewMembership.active,
        ProjectCrewMembership.is_concierge.is_(True),
    ),
)

Project.active_usher_memberships = db.relationship(
    ProjectCrewMembership,
    lazy='dynamic',
    primaryjoin=db.and_(
        ProjectCrewMembership.project_id == Project.id,
        ProjectCrewMembership.active,
        ProjectCrewMembership.is_usher.is_(True),
    ),
)

Project.crew = association_proxy('active_crew_memberships', 'user')
Project.editors = association_proxy('active_editor_memberships', 'user')
Project.concierges = association_proxy('active_concierge_memberships', 'user')
Project.ushers = association_proxy('active_usher_memberships', 'user')

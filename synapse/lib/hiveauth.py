import logging

import synapse.exc as s_exc
import synapse.common as s_common

import synapse.lib.base as s_base
import synapse.lib.cache as s_cache
import synapse.lib.nexus as s_nexus

logger = logging.getLogger(__name__)

class Auth(s_nexus.Pusher):
    '''
    Auth is a user authentication and authorization stored in a Hive.  Users
    correspond to separate logins with different passwords and potentially
    different privileges.

    Users are assigned "rules".  These rules are evaluated in order until a rule
    matches.  Each rule is a tuple of boolean, and a rule path (a sequence of
    strings).  Rules that are prefixes of a privilege match, i.e.  a rule
    ('foo',) will match ('foo', 'bar').

    Roles are just collections of rules.  When a user is "granted" a role those
    rules are assigned to that user.  Unlike in an RBAC system, users don't
    explicitly assume a role; they are merely a convenience mechanism to easily
    assign the same rules to multiple users.

    Authgates are objects that manage their own authorization.  Each
    AuthGate has roles and users subkeys which contain rules specific to that
    user or role for that AuthGate.  The roles and users of an AuthGate,
    called GateRole and GateUser respectively, contain the iden of a role or
    user defined prior and rules specific to that role or user; they do not
    duplicate the metadata of the role or user.

    Node layout:

    Auth root (passed into constructor)
    ├ roles
    │   ├ <role iden 1>
    │   ├ ...
    │   └ last role
    ├ users
    │   ├ <user iden 1>
    │   ├ ...
    │   └ last user
    └ authgates
        ├ <iden 1>
        │   ├ roles
        │   │   ├ <role iden 1>
        │   │   ├ ...
        │   │   └ last role
        │   └ users
        │       ├ <user iden 1>
        │       ├ ...
        │       └ last user
        ├ <iden 2>
        │   ├ ...
        └ ... last authgate
    '''

    async def __anit__(self, node, nexsroot=None):
        '''
        Args:
            node (HiveNode): The root of the persistent storage for auth
        '''
        # Derive an iden from the parent
        iden = 'auth:' + ':'.join(node.full)
        await s_nexus.Pusher.__anit__(self, iden, nexsroot=nexsroot)

        self.node = node

        self.usersbyiden = {}
        self.rolesbyiden = {}
        self.usersbyname = {}
        self.rolesbyname = {}
        self.authgates = {}

        roles = await self.node.open(('roles',))
        for _, node in roles:
            await self._addRoleNode(node)

        users = await self.node.open(('users',))
        for _, node in users:
            await self._addUserNode(node)

        authgates = await self.node.open(('authgates',))
        for _, node in authgates:
            try:
                await self._addAuthGate(node)
            except Exception:  # pragma: no cover
                logger.exception('Failure loading AuthGate')

        self.allrole = await self.getRoleByName('all')
        if self.allrole is None:
            # initialize the role of which all users are a member
            self.allrole = await self.addRole('all')

        # initialize an admin user named root
        self.rootuser = await self.getUserByName('root')
        if self.rootuser is None:
            self.rootuser = await self.addUser('root')

        await self.rootuser.setAdmin(True, logged=False)
        await self.rootuser.setLocked(False, logged=False)

        async def fini():
            await self.allrole.fini()
            await self.rootuser.fini()
            [await u.fini() for u in self.users()]
            [await r.fini() for r in self.roles()]
            [await a.fini() for a in self.authgates.values()]

        self.onfini(fini)

    def users(self):
        return self.usersbyiden.values()

    def roles(self):
        return self.rolesbyiden.values()

    def role(self, iden):
        return self.rolesbyiden.get(iden)

    def user(self, iden):
        return self.usersbyiden.get(iden)

    async def reqUser(self, iden):

        user = self.user(iden)
        if user is None:
            mesg = f'No user with iden {iden}.'
            raise s_exc.NoSuchUser(mesg=mesg)
        return user

    async def reqRole(self, iden):

        role = self.role(iden)
        if role is None:
            mesg = f'No role with iden {iden}.'
            raise s_exc.NoSuchRole(mesg=mesg)
        return role

    async def reqUserByName(self, name):
        user = await self.getUserByName(name)
        if user is None:
            mesg = f'No user named {name}.'
            raise s_exc.NoSuchUser(mesg=mesg)
        return user

    async def reqRoleByName(self, name):
        role = await self.getRoleByName(name)
        if role is None:
            mesg = f'No role named {name}.'
            raise s_exc.NoSuchRole(mesg=mesg)
        return role

    async def getUserByName(self, name):
        '''
        Get a user by their username.

        Args:
            name (str): Name of the user to get.

        Returns:
            HiveUser: A Hive User.  May return None if there is no user by the requested name.
        '''
        return self.usersbyname.get(name)

    async def getUserIdenByName(self, name):
        user = await self.getUserByName(name)
        return None if user is None else user.iden

    async def getRoleByName(self, name):
        return self.rolesbyname.get(name)

    async def _addUserNode(self, node):

        user = await HiveUser.anit(node, self)

        self.usersbyiden[user.iden] = user
        self.usersbyname[user.name] = user

        return user

    @s_nexus.Pusher.onPushAuto('user:name')
    async def setUserName(self, iden, name):

        if self.usersbyname.get(name) is not None:
            raise s_exc.DupUserName(name=name)

        user = await self.reqUser(iden)

        self.usersbyname.pop(user.name, None)
        self.usersbyname[name] = user

        user.name = name
        await user.node.set(name)

    @s_nexus.Pusher.onPushAuto('role:name')
    async def setRoleName(self, iden, name):

        if self.rolesbyname.get(name) is not None:
            raise s_exc.DupRoleName(name=name)

        role = await self.reqRole(iden)

        self.rolesbyname.pop(role.name, None)
        self.rolesbyname[name] = role

        role.name = name
        await role.node.set(name)

    @s_nexus.Pusher.onPushAuto('user:info')
    async def setUserInfo(self, iden, name, valu, gateiden=None):

        user = await self.reqUser(iden)

        info = user.info
        if gateiden is not None:
            info = await user.genGateInfo(gateiden)

        await info.set(name, valu)

        # since any user info *may* effect auth
        user.clearAuthCache()

    @s_nexus.Pusher.onPushAuto('role:info')
    async def setRoleInfo(self, iden, name, valu, gateiden=None):
        role = await self.reqRole(iden)

        info = role.info
        if gateiden is not None:
            info = await role.genGateInfo(gateiden)

        await info.set(name, valu)
        role.clearAuthCache()

    async def _addRoleNode(self, node):

        role = await HiveRole.anit(node, self)

        self.rolesbyiden[role.iden] = role
        self.rolesbyname[role.name] = role

        return role

    async def _addAuthGate(self, node):
        gate = await AuthGate.anit(node, self)
        self.authgates[gate.iden] = gate
        return gate

    async def addAuthGate(self, iden, authgatetype):
        '''
        Retrieve AuthGate by iden.  Create if not present.

        Note:
            Not change distributed

        Returns:
            (HiveAuthGate)
        '''
        gate = self.getAuthGate(iden)
        if gate is not None:
            if gate.type != authgatetype:
                raise s_exc.InconsistentStorage(mesg=f'Stored AuthGate is of type {gate.type}, not {authgatetype}')
            return gate

        path = self.node.full + ('authgates', iden)
        node = await self.node.hive.open(path)
        await self.node.hive.set(path, authgatetype)
        return await self._addAuthGate(node)

    async def delAuthGate(self, iden):
        '''
        Delete AuthGate by iden.

        Note:
            Not change distributed
        '''
        gate = self.getAuthGate(iden)
        if gate is None:
            raise s_exc.NoSuchAuthGate(iden=iden)

        await gate.fini()
        await gate.delete()
        await gate.node.pop()

        del self.authgates[iden]

    def getAuthGate(self, iden):
        return self.authgates.get(iden)

    def reqAuthGate(self, iden):
        gate = self.authgates.get(iden)
        if gate is None:
            mesg = f'No auth gate found with iden: ({iden}).'
            raise s_exc.NoSuchAuthGate(iden=iden, mesg=mesg)
        return gate

    async def addUser(self, name):

        iden = s_common.guid()
        user = await self._push('user:add', iden, name)

        # Everyone's a member of 'all'
        await user.grant('all')

        return user

    @s_nexus.Pusher.onPush('user:add')
    async def _addUser(self, iden, name):

        if self.usersbyname.get(name) is not None:
            raise s_exc.DupUserName(name=name)

        node = await self.node.open(('users', iden))
        await node.set(name)

        return await self._addUserNode(node)

    async def addRole(self, name):
        iden = s_common.guid()
        return await self._push('role:add', iden, name)

    @s_nexus.Pusher.onPush('role:add')
    async def _addRole(self, iden, name):

        if self.rolesbyname.get(name) is not None:
            raise s_exc.DupRoleName(name=name)

        node = await self.node.open(('roles', iden))
        await node.set(name)

        return await self._addRoleNode(node)

    @s_nexus.Pusher.onPushAuto('user:del')
    async def delUser(self, name):

        if name == 'root':
            raise s_exc.CantDelRootUser(mesg='user "root" may not be deleted')

        user = await self.getUserByName(name)
        if user is None:
            raise s_exc.NoSuchUser(name=name)

        self.usersbyiden.pop(user.iden)
        self.usersbyname.pop(user.name)

        path = self.node.full + ('users', user.iden)

        for iden, gate in self.authgates.items():
            await gate._delGateUser(user.iden)

        await user.fini()
        await self.node.hive.pop(path)

    def _getUsersInRole(self, role):
        for user in self.users():
            if role.iden in user.info.get('roles', ()):
                yield user

    @s_nexus.Pusher.onPushAuto('role:del')
    async def delRole(self, name):

        if name == 'all':
            raise s_exc.CantDelAllRole(mesg='role "all" may not be deleted')

        role = self.rolesbyname.get(name)
        if role is None:
            raise s_exc.NoSuchRole(name=name)

        for user in self._getUsersInRole(role):
            await user.revoke(role.name)

        for iden, gate in self.authgates.items():
            await gate._delGateRole(role.iden)

        self.rolesbyiden.pop(role.iden)
        self.rolesbyname.pop(role.name)

        await role.fini()

        # directly set the node's value and let events prop
        path = self.node.full + ('roles', role.iden)
        await self.node.hive.pop(path)

class AuthGate(s_base.Base):
    '''
    The storage object for object specific rules for users/roles.
    '''
    async def __anit__(self, node, auth):
        await s_base.Base.__anit__(self)
        self.auth = auth

        self.iden = node.name()
        self.type = node.valu

        self.node = node

        self.gateroles = {} # iden -> HiveRole
        self.gateusers = {} # iden -> HiveUser

        for useriden, usernode in await node.open(('users',)):

            user = self.auth.user(useriden)
            if user is None:  # pragma: no cover
                logger.warning(f'Hive: path {useriden} refers to unknown user')
                continue

            userinfo = await usernode.dict()
            self.gateusers[user.iden] = user
            user.authgates[self.iden] = userinfo
            user.clearAuthCache()

        for roleiden, rolenode in await node.open(('roles',)):

            role = self.auth.role(roleiden)
            if role is None:  # pragma: no cover
                logger.warning(f'Hive: path {roleiden} refers to unknown role')
                continue

            roleinfo = await rolenode.dict()
            self.gateroles[role.iden] = role
            role.authgates[self.iden] = roleinfo

    async def genUserInfo(self, iden):
        node = await self.node.open(('users', iden))
        userinfo = await node.dict()

        user = self.auth.user(iden)
        self.gateusers[iden] = user
        user.authgates[self.iden] = userinfo

        return userinfo

    async def genRoleInfo(self, iden):
        node = await self.node.open(('roles', iden))
        roleinfo = await node.dict()

        role = self.auth.role(iden)
        self.gateroles[iden] = role
        role.authgates[self.iden] = roleinfo

        return roleinfo

    async def _delGateUser(self, iden):
        self.gateusers.pop(iden, None)
        await self.node.pop(('users', iden))

    async def _delGateRole(self, iden):
        self.gateroles.pop(iden, None)
        await self.node.pop(('roles', iden))

    async def delete(self):

        await self.fini()

        for _, user in list(self.gateusers.items()):
            user.authgates.pop(self.iden, None)
            user.clearAuthCache()

        for _, role in list(self.gateroles.items()):
            role.authgates.pop(self.iden, None)
            role.clearAuthCache()

        await self.node.pop()

class HiveRuler(s_base.Base):
    '''
    A HiveNode that holds a list of rules.  This includes HiveUsers, HiveRoles, and the AuthGate variants of those
    '''

    async def __anit__(self, node, auth):
        await s_base.Base.__anit__(self)

        self.iden = node.name()

        self.auth = auth
        self.node = node
        self.name = node.valu
        self.info = await node.dict()

        self.info.setdefault('admin', False)
        self.info.setdefault('rules', ())

        self.authgates = {}

    async def _setRulrInfo(self, name, valu, gateiden=None): # pragma: no cover
        raise s_exc.NoSuchImpl(mesg='Subclass must implement _setRulrInfo')

    def getRules(self, gateiden=None):

        if gateiden is None:
            return list(self.info.get('rules', ()))

        gateinfo = self.authgates.get(gateiden)
        if gateinfo is None:
            return []

        return list(gateinfo.get('rules', ()))

    async def setRules(self, rules, gateiden=None):
        return await self._setRulrInfo('rules', rules, gateiden=gateiden)

    async def addRule(self, rule, indx=None, gateiden=None):

        rules = self.getRules(gateiden=gateiden)
        assert len(rule) == 2

        if indx is None:
            rules.append(rule)
        else:
            rules.insert(indx, rule)

        await self.setRules(rules, gateiden=gateiden)

    async def delRule(self, rule, gateiden=None):

        rules = self.getRules(gateiden=gateiden)
        if rule not in rules:
            return False

        rules.remove(rule)
        await self.setRules(rules, gateiden=gateiden)
        return True

class HiveRole(HiveRuler):
    '''
    A role within the Hive authorization subsystem.

    A role in HiveAuth exists to bundle rules together so that the same
    set of rules can be applied to multiple users.
    '''
    def pack(self):
        return {
            'type': 'role',
            'iden': self.iden,
            'name': self.name,
            'rules': self.info.get('rules'),
            'authgates': {name: info.pack() for (name, info) in self.authgates.items()},
        }

    async def _setRulrInfo(self, name, valu, gateiden=None):
        return await self.auth.setRoleInfo(self.iden, name, valu, gateiden=gateiden)

    async def setName(self, name):
        return await self.auth.setRoleName(self.iden, name)

    def clearAuthCache(self):
        for user in self.auth.users():
            if user.hasRole(self.iden):
                user.clearAuthCache()

    async def genGateInfo(self, gateiden):
        info = self.authgates.get(gateiden)
        if info is None:
            gate = self.auth.reqAuthGate(gateiden)
            info = self.authgates[gateiden] = await gate.genRoleInfo(self.iden)
        return info

class HiveUser(HiveRuler):
    '''
    A user (could be human or computer) of the system within HiveAuth.

    Cortex-wide rules are stored here.  AuthGate-specific rules for this user are stored in an GateUser.
    '''
    async def __anit__(self, node, auth):
        await HiveRuler.__anit__(self, node, auth)

        self.info.setdefault('roles', ())
        self.info.setdefault('admin', False)
        self.info.setdefault('passwd', None)
        self.info.setdefault('locked', False)
        self.info.setdefault('archived', False)

        # arbitrary profile data for application layer use
        prof = await self.node.open(('profile',))
        self.profile = await prof.dict(nexs=True)

        # TODO: max size check / max count check?
        varz = await self.node.open(('vars',))
        self.vars = await varz.dict(nexs=True)

        self.permcache = s_cache.FixedCache(self._allowed)

    def pack(self):
        return {
            'type': 'user',
            'iden': self.iden,
            'name': self.name,
            'rules': self.info.get('rules', ()),
            'roles': self.info.get('roles', ()),
            'admin': self.info.get('admin', ()),
            'email': self.info.get('email'),
            'locked': self.info.get('locked'),
            'archived': self.info.get('archived'),
            'authgates': {name: info.pack() for (name, info) in self.authgates.items()},
        }

    async def _setRulrInfo(self, name, valu, gateiden=None):
        return await self.auth.setUserInfo(self.iden, name, valu, gateiden=gateiden)

    async def setName(self, name):
        return await self.auth.setUserName(self.iden, name)

    def allowed(self, perm, default=None, gateiden=None):
        return self.permcache.get((perm, default, gateiden))

    def _allowed(self, pkey):

        perm, default, gateiden = pkey

        if self.info.get('locked'):
            return False

        if self.info.get('admin'):
            return True

        # 1. check authgate user rules
        if gateiden is not None:

            info = self.authgates.get(gateiden)
            if info is not None:

                if info.get('admin'):
                    return True

                for allow, path in info.get('rules', ()):
                    if perm[:len(path)] == path:
                        return allow

        # 2. check user rules
        for allow, path in self.info.get('rules', ()):
            if perm[:len(path)] == path:
                return allow

        # 3. check authgate role rules
        if gateiden is not None:

            for role in self.getRoles():

                info = role.authgates.get(gateiden)
                if info is None:
                    continue

                for allow, path in info.get('rules', ()):
                    if perm[:len(path)] == path:
                        return allow

        # 4. check role rules
        for role in self.getRoles():
            for allow, path in role.info.get('rules', ()):
                if perm[:len(path)] == path:
                    return allow

        return default

    def clearAuthCache(self):
        self.permcache.clear()

    async def genGateInfo(self, gateiden):
        info = self.authgates.get(gateiden)
        if info is None:
            gate = self.auth.reqAuthGate(gateiden)
            info = await gate.genUserInfo(self.iden)
        return info

    def confirm(self, perm, default=None, gateiden=None):
        if not self.allowed(perm, default=default, gateiden=gateiden):
            self.raisePermDeny(perm, gateiden=gateiden)

    def raisePermDeny(self, perm, gateiden=None):

        perm = '.'.join(perm)
        if gateiden is None:
            mesg = f'User {self.name!r} ({self.iden}) must have permission {perm}'
            raise s_exc.AuthDeny(mesg=mesg, perm=perm, user=self.name)

        gate = self.auth.reqAuthGate(gateiden)
        mesg = f'User {self.name!r} ({self.iden}) must have permission {perm} on object {gate.iden} ({gate.type}).'
        raise s_exc.AuthDeny(mesg=mesg, perm=perm, user=self.name)

    def getRoles(self):
        for iden in self.info.get('roles', ()):
            role = self.auth.role(iden)
            if role is None:
                logger.warn('user {self.iden} has non-existent role: {iden}')
                continue
            yield role

    def hasRole(self, iden):
        return iden in self.info.get('roles', ())

    async def grant(self, name, indx=None):

        role = self.auth.rolesbyname.get(name)
        if role is None:
            raise s_exc.NoSuchRole(name=name)

        roles = list(self.info.get('roles'))
        if role.iden in roles:
            return

        if indx is None:
            roles.append(role.iden)
        else:
            roles.insert(indx, role.iden)

        await self.auth.setUserInfo(self.iden, 'roles', roles)

    async def revoke(self, name):

        role = self.auth.rolesbyname.get(name)
        if role is None:
            raise s_exc.NoSuchRole(name=name)

        if role.name == 'all':
            raise s_exc.CantRevokeAllRole(mesg='role "all" may not be revoked')

        roles = list(self.info.get('roles'))
        if role.iden not in roles:
            return

        roles.remove(role.iden)
        await self.auth.setUserInfo(self.iden, 'roles', roles)

    def isLocked(self):
        return self.info.get('locked')

    def isAdmin(self, gateiden=None):

        if gateiden is None:
            return self.info.get('admin', False)

        gateinfo = self.authgates.get(gateiden)
        if gateinfo is None:
            return False

        return gateinfo.get('admin', False)

    async def setAdmin(self, admin, gateiden=None, logged=True):
        if logged:
            await self.auth.setUserInfo(self.iden, 'admin', admin, gateiden=gateiden)
        else:
            await self.auth._hndlsetUserInfo(self.iden, 'admin', admin, gateiden=gateiden)

    async def setLocked(self, locked, logged=True):
        if logged:
            await self.auth.setUserInfo(self.iden, 'locked', locked)
        else:
            await self.auth._hndlsetUserInfo(self.iden, 'locked', locked)

    async def setArchived(self, archived):
        await self.auth.setUserInfo(self.iden, 'archived', archived)
        if archived:
            await self.setLocked(True)

    def tryPasswd(self, passwd):

        if self.info.get('locked', False):
            return False

        if passwd is None:
            return False

        shadow = self.info.get('passwd')
        if shadow is None:
            return False

        salt, hashed = shadow

        if s_common.guid((salt, passwd)) == hashed:
            return True

        return False

    async def setPasswd(self, passwd):
        # Prevent empty string or non-string values
        if not passwd or not isinstance(passwd, str):
            raise s_exc.BadArg(mesg='Password must be a string')
        salt = s_common.guid()
        hashed = s_common.guid((salt, passwd))
        await self.info.set('passwd', (salt, hashed))

"""
problog.eval_nodes - Evaluation node classes for the engine_stack
---------------------------------------------------------------------
"""
from problog.engine_builtin import IndirectCallCycleError
from problog.engine_stack import Transformations, SimpleBuiltIn
from problog.engine_unify import substitute_call_args
from problog.errors import GroundingError
from problog.logic import Term, is_ground, ArithmeticError
from .engine import UnknownClauseInternal, UnknownClause, substitute_head_args, OccursCheck, instantiate
from .engine_unify import unify_call_head, UnifyError, unify_call_return


class NegativeCycle(GroundingError):
    """The engine does not support negative cycles."""

    def __init__(self, location=None):
        GroundingError.__init__(self, 'Negative cycle detected', location)


NODE_TRUE = 0
NODE_FALSE = None


class ResultSet(object):
    def __init__(self):
        self.results = []
        self.index = {}
        self.collapsed = False

    def __setitem__(self, result, node):
        index = self.index.get(result)
        if index is None:
            index = len(self.results)
            self.index[result] = index
            if self.collapsed:
                self.results.append((result, node))
            else:
                self.results.append((result, [node]))
        else:
            assert (not self.collapsed)
            self.results[index][1].append(node)

    def __getitem__(self, result):
        index = self.index[result]
        result, node = self.results[index]
        return node

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return None

    def keys(self):
        return [result for result, node in self.results]

    def items(self):
        return self.results

    def __len__(self):
        return len(self.results)

    def collapse(self, function):
        if not self.collapsed:
            for i, v in enumerate(self.results):
                result, node = v
                collapsed_node = function(result, node)
                self.results[i] = (result, collapsed_node)
            self.collapsed = True

    def __contains__(self, key):
        return key in self.index

    def __iter__(self):
        return iter(self.results)

    def __str__(self):  # pragma: no cover
        return str(self.results)


def call(obj, args, kwdargs):
    return 'e', obj, args, kwdargs


def new_result(obj, result, ground_node, source, is_last):
    return 'r', obj, (result, ground_node, source, is_last), {}


def complete(obj, source):
    return 'c', obj, (source,), {}


def results_to_actions(resultlist, engine, node, context, target, parent, identifier,
                       transform, is_root, database, **kwdargs):
    """Translates a list of results to actions.

    :param results:
    :param node:
    :param context:
    :param target:
    :param parent:
    :param identifier:
    :param transform:
    :param is_root:
    :param database:
    :param kwdargs:
    :return:
    """

    # Output
    actions = []

    n = len(resultlist)
    if n > 0:
        # Transform all the results to result messages.
        for result, target_node in resultlist:
            n -= 1
            if not is_root:
                target_node = engine.propagate_evidence(database, target,
                                                        node.functor, result, target_node)
            if target_node != NODE_FALSE:
                if transform:
                    result = transform(result)
                if result is None:
                    if n == 0:
                        actions += [complete(parent, identifier)]
                else:
                    if target_node == NODE_TRUE and target.flag('keep_all') \
                            and not node.functor.startswith('_problog'):
                        name = Term(node.functor, *result)
                        target_node = target.add_atom(name, None, None, name=name, source=None)

                    actions += [new_result(parent, result, target_node, identifier, n == 0)]
            elif n == 0:
                actions += [complete(parent, identifier)]
    else:
        # The goal does not have results: send the completion message.
        actions += [complete(parent, identifier)]
    return actions


class EvalNode(object):
    def __init__(self, engine, database, target, node_id, node, context, parent, pointer,
                 identifier=None, transform=None, call=None, current_clause=None, include=None,
                 exclude=None, no_cache=False, **extra):
        self.engine = engine
        self.database = database
        self.target = target
        self.node_id = node_id
        self.node = node
        self.context = context
        self.parent = parent
        self.identifier = identifier
        self.pointer = pointer
        self.transform = transform
        self.call = call
        self.on_cycle = False
        self.current_clause = current_clause
        self.include = include
        self.exclude = exclude
        self.no_cache = no_cache

    def notifyResult(self, arguments, node=0, is_last=False, parent=None):
        if parent is None:
            parent = self.parent
        if self.transform:
            arguments = self.transform(arguments)
        if arguments is None:
            if is_last:
                return self.notifyComplete()
            else:
                return []
        else:
            return [new_result(parent, arguments, node, self.identifier, is_last)]

    def notifyComplete(self, parent=None):
        if parent is None:
            parent = self.parent
        return [complete(parent, self.identifier)]

    def createCall(self, node_id, *args, **kwdargs):
        base_args = {}
        base_args['database'] = self.database
        base_args['target'] = self.target
        base_args['context'] = self.context
        base_args['parent'] = self.pointer
        base_args['identifier'] = self.identifier
        base_args['transform'] = None
        base_args['call'] = self.call
        base_args['current_clause'] = self.current_clause
        base_args['no_cache'] = self.no_cache
        base_args['include'] = self.include
        base_args['exclude'] = self.exclude
        base_args.update(kwdargs)
        return call(node_id, args, base_args)

    def createCycle(self):
        self.on_cycle = True
        return []

    def node_str(self):  # pragma: no cover
        return str(self.node)

    def __str__(self):  # pragma: no cover
        if hasattr(self.node, 'location'):
            pos = self.database.lineno(self.node.location)
        else:
            pos = None
        if pos is None:
            pos = '??'
        node_type = self.__class__.__name__[4:]
        return '%s %s %s [at %s:%s] | Context: %s' % (
            self.parent, node_type, self.node_str(), pos[0], pos[1], self.context)

    @staticmethod
    def eval(engine, node_id, node, parent=None, context=None, target=None, identifier=None, *args, **kwdargs):
        raise NotImplementedError("Eval not implemented for this node")


class EvalFact(EvalNode):

    def __init__(self, engine, database, target, node_id, node, context, parent, pointer, identifier=None,
                 transform=None, call=None, current_clause=None, include=None, exclude=None, no_cache=False, **extra):
        super().__init__(engine, database, target, node_id, node, context, parent, pointer, identifier, transform, call,
                         current_clause, include, exclude, no_cache, **extra)

    @staticmethod
    def eval(engine, node_id, node, parent=None, context=None, target=None, identifier=None, *args, **kwdargs):
        try:
            # Verify that fact arguments unify with call arguments.
            unify_call_head(context, node.args, context)

            name = Term(node.functor, *node.args)
            # Successful unification: notify parent callback.
            target_node = target.add_atom(node_id, node.probability, name=name)
            if target_node is not None:
                return [
                    new_result(parent, engine.create_context(node.args, parent=context), target_node, identifier,
                               True)]
            else:
                return [complete(parent, identifier)]
        except UnifyError:
            # Failed unification: don't send result.
            # Send complete message.
            return [complete(parent, identifier)]


class EvalCall(EvalNode):

    def __init__(self, engine, database, target, node_id, node, context, parent, pointer, identifier=None,
                 transform=None, call=None, current_clause=None, include=None, exclude=None, no_cache=False, **extra):
        super().__init__(engine, database, target, node_id, node, context, parent, pointer, identifier, transform, call,
                         current_clause, include, exclude, no_cache, **extra)

    @staticmethod
    def eval(engine, node_id, node, parent=None, context=None, target=None, identifier=None, transform=None, *args,
             **kwdargs):
        min_var = engine.context_min_var(context)
        call_args, var_translate = substitute_call_args(node.args, context, min_var)

        if engine.debugger and node.functor != 'call':
            # 'call(X)' is virtual so result and return can not be detected => don't register it.
            location = kwdargs['database'].lineno(node.location)
            engine.debugger.call_create(node_id, node.functor, call_args, parent, location)

        ground_mask = [not is_ground(c) for c in call_args]

        def result_transform(result):
            if hasattr(result, 'state'):
                state1 = result.state
            else:
                state1 = None

            output1 = engine._clone_context(context, state=state1)
            try:
                assert (len(result) == len(node.args))
                output = unify_call_return(result, call_args, output1, var_translate, min_var,
                                           mask=ground_mask)
                output = engine.create_context(output, parent=output1)
                if engine.debugger:
                    location = kwdargs['database'].lineno(node.location)
                    engine.debugger.call_result(node_id, node.functor, call_args, result, location)
                return output
            except UnifyError:
                pass

        if transform is None:
            transform = Transformations()

        transform.addFunction(result_transform)

        origin = '%s/%s' % (node.functor, len(node.args))
        kwdargs['call_origin'] = (origin, node.location)
        kwdargs['context'] = engine.create_context(call_args, parent=context)
        kwdargs['transform'] = transform

        try:
            return engine.eval(node.defnode, parent=parent, identifier=identifier, **kwdargs)
        except UnknownClauseInternal:
            loc = kwdargs['database'].lineno(node.location)
            raise UnknownClause(origin, location=loc)


class EvalChoice(EvalNode):
    def __init__(self, engine, database, target, node_id, node, context, parent, pointer, identifier=None,
                 transform=None, call=None, current_clause=None, include=None, exclude=None, no_cache=False, **extra):
        super().__init__(engine, database, target, node_id, node, context, parent, pointer, identifier, transform, call,
                         current_clause, include, exclude, no_cache, **extra)

    @staticmethod
    def eval(engine, node_id, node, parent=None, context=None, target=None, identifier=None, database=None, *args,
             **kwdargs):
        result = engine._fix_context(context)

        for i, r in enumerate(result):
            if i not in node.locvars and not is_ground(r):
                result = engine.handle_nonground(result=result, node=node, target=target,
                                                 database=database,
                                                 context=context, parent=parent, node_id=node_id,
                                                 identifier=identifier, **kwdargs)

        probability = instantiate(node.probability, result)
        # Create a new atom in ground program.

        if True or engine.label_all:
            if isinstance(node.functor, Term):
                name = node.functor.with_args(*(node.functor.apply(result).args + result))
            else:
                name = Term(node.functor, *result)
        else:
            name = None

        origin = (node.group, result)
        ground_node = target.add_atom(origin + (node.choice,), probability, group=origin, name=name)
        # Notify parent.

        if ground_node is not None:
            return [new_result(parent, result, ground_node, identifier, True)]
        else:
            return [complete(parent, identifier)]


class EvalExtern(EvalNode):
    def __init__(self, engine, database, target, node_id, node, context, parent, pointer, identifier=None,
                 transform=None, call=None, current_clause=None, include=None, exclude=None, no_cache=False, **extra):
        super().__init__(engine, database, target, node_id, node, context, parent, pointer, identifier, transform, call,
                         current_clause, include, exclude, no_cache, **extra)

    @staticmethod
    def eval(engine, node_id, node, parent=None, context=None, target=None, identifier=None, *args, **kwdargs):
        return engine.eval_builtin(node=SimpleBuiltIn(node.function), **kwdargs)


class EvalDefault(EvalNode):
    def __init__(self, engine, database, target, node_id, node, context, parent, pointer, identifier=None,
                 transform=None, call=None, current_clause=None, include=None, exclude=None, no_cache=False, **extra):
        super().__init__(engine, database, target, node_id, node, context, parent, pointer, identifier, transform, call,
                         current_clause, include, exclude, no_cache, **extra)

    @staticmethod
    def eval(engine, node_id, node, parent=None, context=None, target=None, identifier=None, eval_type=None, *args,
             **kwdargs):
        node = eval_type(pointer=engine.pointer, engine=engine, **kwdargs)
        cleanup, actions = node()  # Evaluate the node
        if not cleanup:
            engine.add_record(node)
        return actions


class EvalClause(EvalNode):

    def __init__(self, engine, database, target, node_id, node, context, parent, pointer, identifier=None,
                 transform=None, call=None, current_clause=None, include=None, exclude=None, no_cache=False, **extra):
        super().__init__(engine, database, target, node_id, node, context, parent, pointer, identifier, transform, call,
                         current_clause, include, exclude, no_cache, **extra)

    @staticmethod
    def eval(engine, node_id, node, parent=None, context=None, target=None, transform=None, identifier=None,
             current_clause=None, *args,
             **kwdargs):
        new_context = engine.create_context([None] * node.varcount, parent=context)

        try:
            try:
                unify_call_head(context, node.args, new_context)
            except OccursCheck as err:
                raise OccursCheck(location=kwdargs['database'].lineno(node.location))

            # Note: new_context should not contain None values.
            # We should replace these with negative numbers.
            # 1. Find lowest negative number in new_context.
            #   TODO better option is to store this in context somewhere
            min_var = engine.context_min_var(new_context)
            # 2. Replace None in new_context with negative values
            cc = min_var
            for i, c in enumerate(new_context):
                if c is None:
                    cc -= 1
                    new_context[i] = cc
            if transform is None:
                transform = Transformations()

            def result_transform(result):
                output = substitute_head_args(node.args, result)
                return engine.create_context(output, parent=result)

            transform.addFunction(result_transform)
            return engine.eval(node.child, context=new_context, parent=parent, transform=transform,
                               current_clause=node_id, identifier=identifier, **kwdargs)
        except UnifyError:
            # Call and clause head are not unifiable, just fail (complete without results).
            return [complete(parent, identifier)]


class EvalOr(EvalNode):
    # Has exactly one listener (parent)
    # Has C children.
    # Behaviour:
    # - 'call' creates child nodes and request calls to them
    # - 'call' calls complete if there are no children (C = 0)
    # - 'new_result' is forwarded to parent
    # - 'complete' waits until it is called C times, then sends signal to parent
    # Can be cleanup after 'complete' was sent

    def __init__(self, **parent_args):
        EvalNode.__init__(self, **parent_args)
        self.results = ResultSet()
        if not self.is_buffered():
            self.flushBuffer(True)
        self.to_complete = len(self.node.children)
        self.engine.stats[0] += 1

    def is_buffered(self):
        return not (self.on_cycle or self.engine.unbuffered)

    def isOnCycle(self):
        return self.on_cycle

    def flushBuffer(self, cycle=False):
        func = lambda result, nodes: self.target.add_or(nodes, readonly=(not cycle), name=None)
        self.results.collapse(func)

    def new_result(self, result, node=NODE_TRUE, source=None, is_last=False):
        if not self.is_buffered():
            res = self.engine._fix_context(result)
            assert self.results.collapsed
            if res in self.results:
                res_node = self.results[res]
                self.target.add_disjunct(res_node, node)
                if is_last:
                    return self.complete(source)
                else:
                    return False, []
            else:
                result_node = self.target.add_or((node,), readonly=False, name=None)
                self.results[res] = result_node
                actions = self.notifyResult(res, result_node)
                if is_last:
                    a, act = self.complete(source)
                    actions += act
                else:
                    a = False
                return a, actions
        else:
            assert (not self.results.collapsed)
            res = self.engine._fix_context(result)
            self.results[res] = node
            if is_last:
                return self.complete(source)
            else:
                return False, []

    def complete(self, source=None):
        self.to_complete -= 1
        if self.to_complete == 0:
            self.flushBuffer()
            actions = []
            if self.is_buffered():
                for result, node in self.results:
                    actions += self.notifyResult(result, node)
            actions += self.notifyComplete()
            return True, actions
        else:
            return False, []

    def createCycle(self):
        if self.is_buffered():
            self.on_cycle = True
            self.flushBuffer(True)
            actions = []
            for result, node in self.results:
                actions += self.notifyResult(result, node)
            return actions
        else:
            self.on_cycle = True
            return []

    def node_str(self):  # pragma: no cover
        return ''

    def __str__(self):  # pragma: no cover
        return EvalNode.__str__(self) + ' tc: ' + str(self.to_complete)

    @staticmethod
    def eval(engine, node_id, node, parent=None, context=None, target=None, identifier=None, *args, **kwdargs):
        if len(node.children) == 0:
            # No children, so complete immediately.
            return [complete(parent, None)]
        else:
            evalnode = EvalOr(pointer=engine.pointer, engine=engine, parent=parent, node=node,
                              **kwdargs)
            engine.add_record(evalnode)
            return [evalnode.createCall(child) for child in node.children]


class EvalDefine(EvalNode):
    # A buffered Define node.
    def __init__(self, call=None, to_complete=None, is_root=False, **parent_args):
        EvalNode.__init__(self, **parent_args)
        # self.__buffer = defaultdict(list)
        # self.results = None

        self.results = ResultSet()

        self.cycle_children = []
        self.cycle_close = set()
        self.is_cycle_root = False
        self.is_cycle_child = False
        self.is_cycle_parent = False
        self.siblings = []  # These are nodes that have is_cycle_child set

        self.call = (self.node.functor, self.context)
        self.to_complete = to_complete
        self.is_ground = is_ground(*self.context)
        self.is_root = is_root
        self.engine.stats[1] += 1

        if not self.is_buffered():
            self.flushBuffer(True)

    def notifyResult(self, arguments, node=0, is_last=False, parent=None):
        if not self.is_root:
            node = self.engine.propagate_evidence(self.database, self.target, self.node.functor,
                                                  arguments, node)
        return super(EvalDefine, self).notifyResult(arguments, node, is_last, parent)

    def notifyResultMe(self, arguments, node=0, is_last=False):
        parent = self.pointer
        return [new_result(parent, arguments, node, self.identifier, is_last)]

    def notifyResultSiblings(self, arguments, node=0, is_last=False):
        parents = self.siblings
        return [new_result(parent, arguments, node, self.identifier, is_last) for parent in parents]

    def notifyResultChildren(self, arguments, node=0, is_last=False):
        parents = self.cycle_children
        return [new_result(parent, arguments, node, self.identifier, is_last) for parent in parents]

    def new_result(self, result, node=NODE_TRUE, source=None, is_last=False):
        if self.is_ground and node == NODE_TRUE and not self.target.flag('keep_all'):
            # We have a ground node with a deterministically true proof.
            # We can ignore the remaining proofs.
            # self.engine.ignoring.add(self.pointer)
            pass  # not when there is state
        if self.is_cycle_child:
            assert not self.siblings
            if is_last:
                return True, self.notifyResult(result, node, is_last=is_last)
            else:
                return False, self.notifyResult(result, node, is_last=is_last)
        else:
            if not self.is_buffered() or self.isCycleParent():
                assert self.results.collapsed
                res = self.engine._fix_context(result)
                res_node = self.results.get(res)
                if res_node is not None:
                    self.target.add_disjunct(res_node, node)
                    actions = []
                    if is_last:
                        a, act = self.complete(source)
                        actions += act
                    else:
                        a = False
                    return a, actions
                else:
                    cache_key = self.call  # (self.node.functor, res)
                    if not self.no_cache and cache_key in self.target._cache:
                        # Get direct
                        stored_result = self.target._cache[cache_key]
                        assert (len(stored_result) == 1)
                        result_node = stored_result[0][1]

                        if not self.is_root:
                            result_node = self.engine.propagate_evidence(self.database, self.target, self.node.functor,
                                                                         res, result_node)
                    else:
                        if self.engine.label_all:
                            name = Term(self.node.functor, *res)
                        else:
                            name = None
                            if not self.is_root:
                                node = self.engine.propagate_evidence(self.database, self.target, self.node.functor,
                                                                      res, node)
                        result_node = self.target.add_or((node,), readonly=False, name=name)
                    self.results[res] = result_node
                    if not self.no_cache and is_ground(*res) and is_ground(*self.call[1]):
                        self.target._cache[cache_key] = {res: result_node}
                    actions = []
                    # Send results to cycle
                    if not self.is_buffered() and result_node is not NODE_FALSE:
                        actions += self.notifyResult(res, result_node)

                    # TODO what if result_node is NONE?
                    actions += self.notifyResultChildren(res, result_node, is_last=False)
                    actions += self.notifyResultSiblings(res, result_node, is_last=False)
                    # TODO the following optimization doesn't always work, see test/some_cycles.pl
                    # actions += self.notifyResultChildren(res, result_node, is_last=self.is_ground)
                    # if self.is_ground :
                    #     self.engine.cycle_root.cycle_close -= set(self.cycle_children)

                    if is_last:
                        a, act = self.complete(source)
                        actions += act
                    else:
                        a = False
                    return a, actions
            else:
                assert (not self.results.collapsed)
                res = self.engine._fix_context(result)
                self.results[res] = node
                if is_last:
                    return self.complete(source)
                else:
                    return False, []

    def complete(self, source=None):
        if self.is_cycle_child:
            assert (not self.siblings)
            return True, self.notifyComplete()
        else:
            self.to_complete -= 1
            if self.to_complete == 0:
                cache_key = self.call
                # cache_key = (self.node.functor, self.context)
                # assert (not cache_key in self.target._cache)
                self.flushBuffer()
                self.target._cache[cache_key] = self.results
                self.target._cache.deactivate(cache_key)
                actions = []
                if self.is_buffered():
                    actions = results_to_actions(self.results, **vars(self))

                    for s in self.siblings:
                        n = len(self.results)
                        if n > 0:
                            for arg, rn in self.results:
                                n -= 1
                                actions.append(new_result(s, arg, rn, self.identifier, n == 0))
                        else:
                            actions += [complete(s, self.identifier)]
                else:
                    actions += self.notifyComplete()
                    for s in self.siblings:
                        actions += self.notifyComplete(parent=s)
                return True, actions
            else:
                return False, []

    def flushBuffer(self, cycle=False):
        def func(res, nodes):
            cache_key = self.call
            #            cache_key = (self.node.functor, res)
            if not self.no_cache and cache_key in self.target._cache:
                stored_result = self.target._cache[cache_key]
                if len(stored_result) == 1:
                    node = stored_result[0][1]
                else:
                    if self.engine.label_all:
                        name = Term(self.node.functor, *res)
                    else:
                        name = None
                    node = self.target.add_or([y for x, y in stored_result], name=name)
                # assert (len(stored_result) == 1)
                if not self.is_root:
                    node = self.engine.propagate_evidence(self.database, self.target, self.node.functor, res, node)
            else:
                if self.engine.label_all:
                    name = Term(self.node.functor, *res)
                else:
                    name = None

                if not self.is_root:
                    new_nodes = []
                    for node in nodes:
                        node = self.engine.propagate_evidence(self.database, self.target, self.node.functor, res, node)
                        new_nodes.append(node)
                    nodes = new_nodes
                node = self.target.add_or(nodes, readonly=(not cycle), name=name)
                if not self.no_cache and is_ground(*res) and is_ground(*self.call[1]):
                    self.target._cache[cache_key] = {res: node}

            return node

        self.results.collapse(func)

    def is_buffered(self):
        return not (self.on_cycle or self.engine.unbuffered)

    def isOnCycle(self):
        return self.on_cycle

    def isCycleParent(self):
        return bool(self.cycle_children) or self.is_cycle_parent

    def cycleDetected(self, cycle_parent):
        queue = []
        cycle = self.engine.find_cycle(self.pointer, cycle_parent.pointer, cycle_parent.isCycleParent())

        if not cycle:
            cycle_parent.siblings.append(self.pointer)
            self.is_cycle_child = True
            for result, node in cycle_parent.results:
                if type(node) == list:
                    raise IndirectCallCycleError()
                queue += self.notifyResultMe(result, node)
                queue += self.notifyResultMe(result, node)
        else:
            # goal = (self.node.functor, self.context)
            # Get the top node of this cycle.
            cycle_root = self.engine.cycle_root
            # Mark this node as a cycle child
            self.is_cycle_child = True
            # Register this node as a cycle child of cycle_parent
            cycle_parent.cycle_children.append(self.pointer)

            cycle_parent.flushBuffer(True)
            for result, node in cycle_parent.results:
                queue += self.notifyResultMe(result, node)

            if cycle_root is not None and cycle_parent.pointer < cycle_root.pointer:
                # New parent is earlier in call stack as current cycle root
                # Unset current root
                # Unmark current cycle root
                cycle_root.is_cycle_root = False
                # Copy subcycle information from old root to new root
                cycle_parent.cycle_close = cycle_root.cycle_close
                cycle_root.cycle_close = set()
                self.engine.cycle_root = cycle_parent
                queue += cycle_root.createCycle()
                queue += self.engine.notify_cycle(cycle)
                # queue += self.engine.notifyCycle(cycle_root)
                # cycle_root)  # Notify old cycle root up to new cycle root
                cycle_root = None
            if cycle_root is None:  # No active cycle
                # Register cycle root with engine
                self.engine.cycle_root = cycle_parent
                # Mark parent as cycle root
                cycle_parent.is_cycle_root = True
                #
                cycle_parent.cycle_close.add(self.pointer)
                # Queue a create cycle message that will be passed up the call stack.
                queue += self.engine.notify_cycle(cycle)
                # queue += self.engine.notifyCycle(self)
                # Send a close cycle message to the cycle root.
            else:  # A cycle is already active
                # The new one is a subcycle
                # if not self.is_ground :
                cycle_root.cycle_close.add(self.pointer)
                # Queue a create cycle message that will be passed up the call stack.
                queue += self.engine.notify_cycle(cycle)
                if cycle_parent.pointer != self.engine.cycle_root.pointer:
                    to_cycle_root = self.engine.find_cycle(cycle_parent.pointer, self.engine.cycle_root.pointer)
                    if to_cycle_root is None:
                        raise IndirectCallCycleError(self.database.lineno(self.node.location))
                    queue += cycle_parent.createCycle()
                    queue += self.engine.notify_cycle(to_cycle_root)
        return queue

    def closeCycle(self, toplevel):
        if self.is_cycle_root and toplevel:
            self.engine.cycle_root = None
            actions = []
            for cc in self.cycle_close:
                actions += self.notifyComplete(parent=cc)
            return actions
        else:
            return []

    def createCycle(self):
        if self.on_cycle:  # Already on cycle
            # Pass message to parent
            return []
        elif self.is_cycle_root:
            return []
        elif self.is_buffered():
            # Define node
            self.on_cycle = True
            self.flushBuffer(True)
            actions = []
            for result, node in self.results:
                actions += self.notifyResult(result, node)
                for s in self.siblings:
                    actions += self.notifyResultSiblings(result, node)
            return actions
        else:
            self.on_cycle = True
            return []

    def node_str(self):  # pragma: no cover
        return str(Term(self.node.functor, *self.context))

    def __str__(self):  # pragma: no cover
        extra = ['tc: %s' % self.to_complete]
        if self.is_cycle_child:
            extra.append('CC')
        if self.is_cycle_root:
            extra.append('CR')
        if self.isCycleParent():
            extra.append('CP')
        if self.on_cycle:
            extra.append('*')
        if self.cycle_children:
            extra.append('c_ch: %s' % (self.cycle_children,))
        if self.cycle_close:
            extra.append('c_cl: %s' % (self.cycle_close,))
        if self.siblings:
            extra.append('sbl: %s' % (self.siblings,))
        if not self.is_buffered():
            extra.append('U')
        return EvalNode.__str__(self) + ' ' + ' '.join(extra)

    @staticmethod
    def eval(engine, node_id, node, parent=None, context=None, target=None, identifier=None, transform=None,
             is_root=False, no_cache=False, *args, **kwdargs):

        # This function evaluates the 'define' nodes in the database.
        # This is basically the same as evaluating a goal in Prolog.
        # There are three possible situations:
        #   - the goal has been evaluated before (it is in cache)
        #   - the goal is currently being evaluated (i.e. we have a cycle)
        #        we make a distinction between ground goals and non-ground goals
        #   - we have not seen this goal before

        # Extract a descriptor for the current goal being evaluated.
        functor = node.functor
        goal = (functor, context)
        # Look up the results in the cache.
        if no_cache:
            results = None
        else:
            results = target._cache.get(goal)
        if results is not None:
            # We have results for this goal, i.e. it has been fully evaluated before.
            # Transform the results to actions and return.
            return results_to_actions(results, engine, node, context, target, parent, identifier, transform, is_root,
                                      **kwdargs)
        else:
            # Look up the results in the currently active nodes.
            active_node = target._cache.getEvalNode(goal)
            if active_node is not None:
                # There is an active node.
                if active_node.is_ground and active_node.results:
                    # If the node is ground, we can simply return the current result node.
                    active_node.flushBuffer(True)
                    active_node.is_cycle_parent = True  # Notify it that it's buffer was flushed
                    queue = results_to_actions(active_node.results, engine, node, context, target, parent, identifier,
                                               transform, is_root, **kwdargs)
                    assert (len(queue) == 1)
                    engine.checkCycle(parent, active_node.pointer)
                    return queue
                else:
                    # If the node in non-ground, we need to create an evaluation node.
                    evalnode = EvalDefine(pointer=engine.pointer, engine=engine, node=node,
                                          context=context, target=target, identifier=identifier,
                                          parent=parent, transform=transform, is_root=is_root,
                                          no_cache=no_cache,
                                          **kwdargs)
                    engine.add_record(evalnode)
                    return evalnode.cycleDetected(active_node)
            else:
                # The node has not been seen before.
                # Get the children that may fit the context (can contain false positives).
                children = node.children.find(context)
                to_complete = len(children)

                if to_complete == 0:
                    # No children, so complete immediately.
                    return [complete(parent, identifier)]
                else:
                    # Children to evaluate, so start evaluation node.
                    evalnode = EvalDefine(to_complete=to_complete, pointer=engine.pointer,
                                          engine=self, node=node, context=context, target=target,
                                          identifier=identifier, transform=transform, parent=parent,
                                          no_cache=no_cache,
                                          **kwdargs)
                    engine.add_record(evalnode)
                    target._cache.activate(goal, evalnode)
                    actions = [evalnode.createCall(child) for child in children]
                    return actions


class EvalNot(EvalNode):
    # Has exactly one listener (parent)
    # Has 1 child.
    # Behaviour:
    # - 'new_result' stores results and does not request actions
    # - 'complete: sends out new_results and complete signals
    # Can be cleanup after 'complete' was sent

    def __init__(self, **parent_args):
        EvalNode.__init__(self, **parent_args)
        self.nodes = set()  # Store ground nodes
        self.engine.stats[2] += 1

    def __call__(self):
        return False, [self.createCall(self.node.child)]

    def new_result(self, result, node=NODE_TRUE, source=None, is_last=False):
        if node != NODE_FALSE:
            self.nodes.add(node)
        if is_last:
            return self.complete(source)
        else:
            return False, []

    def complete(self, source=None):
        actions = []
        if self.nodes:
            or_node = self.target.add_not(self.target.add_or(self.nodes, name=None))
            if or_node != NODE_FALSE:
                actions += self.notifyResult(self.context, or_node)
        else:
            if self.target.flag('keep_all'):
                src_node = self.database.get_node(self.node.child)
                min_var = self.engine.context_min_var(self.context)
                if type(src_node).__name__ == 'atom':
                    args, _ = substitute_call_args(src_node.args, self.context, min_var=min_var)
                    name = Term(src_node.functor, *args)
                else:
                    name = None
                node = -self.target.add_atom(name, False, None, name=name, source='negation')
            else:
                node = NODE_TRUE

            actions += self.notifyResult(self.context, node)
        actions += self.notifyComplete()
        return True, actions

    def createCycle(self):
        raise NegativeCycle(location=self.database.lineno(self.node.location))

    def node_str(self):  # pragma: no cover
        return ''

    @staticmethod
    def eval(engine, node_id, node, parent=None, context=None, target=None, identifier=None, *args, **kwdargs):
        return engine.eval_default(EvalNot, **kwdargs)


class EvalAnd(EvalNode):
    def __init__(self, **parent_args):
        EvalNode.__init__(self, **parent_args)
        self.to_complete = 1
        self.engine.stats[3] += 1

    def __call__(self):
        return False, [self.createCall(self.node.children[0], identifier=None)]

    def new_result(self, result, node=0, source=None, is_last=False):
        if source is None:  # Result from the first conjunct.
            # We will create a second conjunct, which needs to send a 'complete' signal.
            self.to_complete += 1
            if is_last:
                # Notify self that this conjunct is complete. ('all_complete' will always be False)
                all_complete, complete_actions = self.complete()
                # if False and node == NODE_TRUE :
                #     # TODO THERE IS A BUG HERE
                #     # If there is only one node to complete (the new second conjunct) then
                #     #  we can clean up this node, but then we would lose the ground node of
                #     #  the first conjunct.
                #     # This is ok when it is deterministically true.  TODO make this always ok!
                #     # We can redirect the second conjunct to our parent.
                #     return (self.to_complete==1),
                # [ self.createCall( self.node.children[1], context=result, parent=self.parent ) ]
                # else :
                return False, [
                    self.createCall(self.node.children[1], context=result, identifier=node)]
            else:
                # Not the last result: default behaviour
                return False, [
                    self.createCall(self.node.children[1], context=result, identifier=node)]
        else:  # Result from the second node
            # Make a ground node
            target_node = self.target.add_and((source, node), name=None)
            if is_last:
                # Notify self of child completion
                all_complete, complete_actions = self.complete()
            else:
                all_complete, complete_actions = False, []
            if all_complete:
                return True, self.notifyResult(result, target_node, is_last=True)
            else:
                return False, self.notifyResult(result, target_node, is_last=False)

    def complete(self, source=None):
        self.to_complete -= 1
        if self.to_complete == 0:
            return True, self.notifyComplete()
        else:
            assert (self.to_complete > 0)
            return False, []

    def node_str(self):  # pragma: no cover
        return ''

    def __str__(self):  # pragma: no cover
        return EvalNode.__str__(self) + ' tc: %s' % self.to_complete

    @staticmethod
    def eval(engine, node_id, node, parent=None, context=None, target=None, identifier=None, *args, **kwdargs):
        return engine.eval_default(EvalAnd, **kwdargs)


class EvalBuiltIn(EvalNode):
    def __init__(self, call_origin=None, **kwdargs):
        EvalNode.__init__(self, **kwdargs)
        if call_origin is not None:
            self.location = call_origin[1]
        else:
            self.location = None
        self.call_origin = call_origin
        self.engine.stats[4] += 1

    def __call__(self):
        try:
            return self.node(*self.context, engine=self.engine, database=self.database,
                             target=self.target, location=self.location, callback=self,
                             transform=self.transform, parent=self.parent, context=self.context,
                             identifier=self.identifier, call_origin=self.call_origin,
                             current_clause=self.current_clause)
        except ArithmeticError as err:
            if self.database and self.location:
                functor = self.call_origin[0].split('/')[0]
                callterm = Term(functor, *self.context)
                base_message = 'Error while evaluating %s: %s' % (callterm, err.base_message)
                location = self.database.lineno(self.location)
                raise ArithmeticError(base_message, location)
            else:
                raise err

    @staticmethod
    def eval(engine, node_id, node, parent=None, context=None, target=None, identifier=None, *args, **kwdargs):
        return engine.eval_default(EvalBuiltIn, **kwdargs)
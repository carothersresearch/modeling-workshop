import numpy as np
from optlang.symbolics import add
import pandas as pd
import cplex

#################################################################
########Functions needed for all prediction methods##############
#################################################################
"""
    Creates dictionary of isozymes by parsing GPR:
    Parse GPR into a dict containing isozymes (separated by 'or'). Each isozyme has a set of subunits (separated by 'and') 'and' and 'or' can occur at the same time, or can occur by itself.
    
        Parameters
        ----------
        model : cobrapy model.
        
        
        Returns
        -------
        gpr_dict: dictionary with isozymes.
        
"""
#Code only works for GPRs written in disjunctive normal form (DNF). Majority of models have them in DNF but there are some exceptions. 

def create_gprdict(model):   
    gpr_dict = dict()
    for rxn in model.reactions:
        if rxn.gene_reaction_rule:
            temp = set()
            for x in [x.strip('() ') for x in rxn.gene_reaction_rule.split(' or ')]:
                temp.add(frozenset(y.strip('() ') for y in x.split(' and ')))
            gpr_dict[rxn.id] = temp
    return gpr_dict

"""
    Calculates bound value based on transcriptomics data for reactions in gene reaction rule
    
    NOTE: 
    If a reaction R1 has the GPR of 'A and B', it would be parsed to { {A, B} } in gpr_dict['R1']. Then t for R1 would be sum( [ min(A, B) ] ) = min(A, B).
    If a reaction R1 has the GPR of 'A or B', it would be parsed to { {A}, {B} } in gpr_dict['R1']. Then t for R1 would be sum( [ min(A), min(B) ] ) = sum( [A, B] ).
    If a reaction R1 has the GPR of '(A and B) or (C and D)', it would be parsed to { {A, B}, {C, D} } in gpr_dict['R1']. Then t for R1 would be sum( [ min(A, B), min(C, D) ] ).
    
        Parameters
        ----------
        model : cobrapy model.
        Transcriptomics : pandas dataframe with transcriptomics data.Data frame has gene identifiers as index and just one column with transcript values.  
        rxn : cobrapy model reaction
        
        
        Returns
        -------
        transscript bound value: float.
"""

def transcript_value_for_rxn(model, transcriptomics_df, rxn):
    final_transcript_value = 0
    gene_ids = []
    for parallel_gene in create_gprdict(model)[rxn.id]:
        transcript_values = []
        for gene in parallel_gene:
            if gene in transcriptomics_df.index:
                print(gene)
                print(type(transcriptomics_df.loc[gene]))
                print(transcriptomics_df.loc[gene].shape)
                transcript_values.append(transcriptomics_df.loc[gene][0])
                # transcript_values.append(transcriptomics_df.loc[gene].to_numpy()[0])
#                 print(transcriptomics_df.loc[gene].to_numpy()[0])
            else:
                transcript_values.append(np.inf)
            min_transcript_val = np.min(transcript_values)
        final_transcript_value = final_transcript_value + min_transcript_val
#         if final_transcript_value==newinfbound:
#             display(rxn.id)
#             gene_ids.append(rxn.id)
    return final_transcript_value

#############################################
##################EFLUX2#######################
############################################
"""
    Provides EFLUX2 predictions as explained in Machado et. al (2014) 
    
        Parameters
        ----------
        model : cobrapy model.
        Transcriptomics : pandas dataframe with transcriptomics data.Data frame has gene identifiers as index and just one column with transcript values.  
        
        Returns
        -------
        eflux2_sol as output from eflux2_model.optimize().
        
"""
def EFlux2(model, Transcriptomics):
    # copy model and set tolerance
    eflux2_model = model.copy()
    eflux2_model.tolerance = 1e-9
    
    # set the flux bounds for each reaction using the transcriptomics data    
    for rxn in eflux2_model.reactions:
        if 'EX_' not in str(rxn.id):
            if rxn.gene_reaction_rule:
                if rxn.lower_bound < 0.0:
                    rxn.lower_bound = -transcript_value_for_rxn(model, Transcriptomics, rxn)
                else:
                    pass
                if rxn.upper_bound > 0.0:
                    rxn.upper_bound = transcript_value_for_rxn(model, Transcriptomics, rxn)
                else:
                    pass
            else:
                """When there is no GPR, the arbitrary bounds are removed. 
                Common arbitrary bound value of 1000 for E.coli, might be different depending on the model, e.g., 99999.0 for iMM904 yeast model in BiGG"""
                if rxn.lower_bound <= -1000:
                    rxn.lower_bound = -np.Inf
                if rxn.upper_bound >= 1000:
                    rxn.upper_bound = np.Inf 
    
    # solve FBA problem with transcriptomic bounds
    fba_solution = eflux2_model.optimize()
    print('FBA status', fba_solution.status)
    print('FBA solution', fba_solution.objective_value)
    
    display(eflux2_model.summary(solution=fba_solution))

    # constrain the biomass to the optimal value
    for r in eflux2_model.reactions:
        if r.objective_coefficient:
            r.lower_bound = fba_solution.objective_value
            r.upper_bound = fba_solution.objective_value

    # Inspect media
#     display(eflux2_model.medium)
    
    # Minimize the sum of squared flux values
    """Note: Because of quadratic objective still have to use cplex objective formulation.
    Optlang does not support quadratic type of constraints and objectives yet."""
    eflux2_model.objective = eflux2_model.problem.Objective(add([rxn.flux_expression**2 for rxn in eflux2_model.reactions]), direction='min')
    
    # solve the minimization of squared fluxes problem
    EFlux2_solution = eflux2_model.optimize()
    
    #display(eflux2_model.summary())
    display(eflux2_model.summary(solution=EFlux2_solution))

    
    print('E-Flux2 status', EFlux2_solution.status)
    print('E-Flux2 solution', EFlux2_solution.objective_value)
    print()
        
    return EFlux2_solution



#############################################
##################SPOT#######################
############################################

"""
    Provides SPOT predictions as explained in Machado et. al (2014) using cplex python library. If optlang enhanced such that quadratic constraints are supported, we should be able to switch to formulation without using cplex (spot.py code). 
    Calls create_gprdict function.
    
        Parameters
        ----------
        model : cobrapy model.
        Transcriptomics : pandas dataframe with transcriptomics data. Data frame has gene identifiers as index and just one column with transcript values.  
        
        Returns
        -------
        sol as output from optimization of SPOT model via CPLEX formulation.
        
"""
def SPOT(model, Transcriptomics):
    
    mets = [met.id for met in model.metabolites]
    rxns = [rxn.id for rxn in model.reactions]
    nrow = len(mets)
    ncol = len(rxns)
    
        #"However, if any of the flux bounds does not include zero, the origin in the graph, the maximum correlation 
    #is no longer independent of the length of the fluxvector. Thus, it is a prerequisite 
    #for using SPOT method to make sure that the allowable solution space includes
    #the origin."(Supplementary file S1 to EFLUX2 SPOT Paper)
    for r in model.reactions:
        if r.lower_bound < 0.0 and r.lower_bound > -1000.0:
            print(r.id, r.lower_bound, r.upper_bound)
            r.lower_bound = -1000.0
        elif r.lower_bound > 0.0:
            print(r.id, r.lower_bound, r.upper_bound)
            r.lower_bound = 0.0
        elif r.upper_bound > 0.0 and r.upper_bound < 1000.0:
            print(r.id, r.lower_bound, r.upper_bound)
            r.upper_bound = 1000.0
        elif r.upper_bound < 0.0:
            print(r.id, r.lower_bound, r.upper_bound)
            r.upper_bound = 0.0

    for r in model.reactions:
        if r.lower_bound == -1000.0:
            r.lower_bound = -np.Inf
        if r.upper_bound == 1000.0:
            r.upper_bound = np.Inf

    rev_rxns = ['rev_'+rxn.id for rxn in model.reactions if rxn.reversibility]
    rev_ncol = len(rev_rxns)
    
    """Parse GPR into a dict containing isozymes (separated by 'or')
    # Each isozyme has a set of subunits (separated by 'and')
    #'and' and 'or' can occur at the same time, or can occur by itself."""
    #gpr_dict = create_gprdict(model)

    lb = [0.0 if rxn.reversibility else rxn.lower_bound for rxn in model.reactions] + [0.0 for rxn in model.reactions if rxn.reversibility]
    ub = [rxn.upper_bound for rxn in model.reactions] + [-rxn.lower_bound for rxn in model.reactions if rxn.reversibility]
        
    c = []
    for rxn in model.reactions:
        #if 'EX_' not in str(rxn):
        if rxn.gene_reaction_rule:
        #If a reaction R1 has the GPR of 'A and B', it would be parsed to { {A, B} } in gpr_dict['R1']. Then t for R1 would be sum( [ min(A, B) ] ) = min(A, B).
        #If a reaction R1 has the GPR of 'A or B', it would be parsed to { {A}, {B} } in gpr_dict['R1']. Then t for R1 would be sum( [ min(A), min(B) ] ) = sum( [A, B] ).
        #If a reaction R1 has the GPR of '(A and B) or (C and D)', it would be parsed to { {A, B}, {C, D} } in gpr_dict['R1']. Then t for R1 would be sum( [ min(A, B), min(C, D) ] ).

            transboundval = transcript_value_for_rxn(model, Transcriptomics,rxn)
            if transboundval == np.Inf:
                transboundval = 0.0
            c.append(transboundval)
        else:
            c.append(0.0)
    for rxn in model.reactions:
        if rxn.reversibility:# and 'EX_' not in str(rxn):
            if rxn.gene_reaction_rule:
                transboundval = transcript_value_for_rxn(model, Transcriptomics,rxn)
                if transboundval == np.Inf:
                    transboundval = 0.0
                c.append(transboundval)
            else:
                c.append(0.0)

    SPOT = cplex.Cplex()
    SPOT.set_results_stream(None)
    SPOT.parameters.simplex.tolerances.optimality.set(1e-9)
    SPOT.parameters.simplex.tolerances.feasibility.set(1e-9)
    SPOT.parameters.barrier.qcpconvergetol.set(1e-12)
    

    SPOT.linear_constraints.add(rhs=[0]*nrow, senses='E'*nrow, names=mets)
    SPOT.variables.add(obj=c, lb=lb, ub=ub, names=rxns+rev_rxns)
    for rxn in model.reactions:
        for m, v in rxn.metabolites.items():
            SPOT.linear_constraints.set_coefficients(m.id, rxn.id, v)
    for rxn in model.reactions:
        if rxn.reversibility:
            for m, v in rxn.metabolites.items():
                SPOT.linear_constraints.set_coefficients(m.id, 'rev_'+rxn.id, -v)
    SPOT.quadratic_constraints.add(quad_expr=[rxns+rev_rxns, rxns+rev_rxns, [1]*len(c)],
                                   sense='L', rhs=1.0, name='L2norm')#L indicating <=
    SPOT.objective.set_sense(SPOT.objective.sense.maximize)
    SPOT.solve()

    SPOT_sol = SPOT.solution.get_objective_value()

    sol = type('',(),{})()
    temp = pd.Series(data=SPOT.solution.get_values(), index=rxns+rev_rxns)
    flux = temp.loc[rxns]
    flux_rev = temp.loc[rev_rxns]
    for rxn in model.reactions:
        if rxn.reversibility:
            flux.loc[rxn.id] = flux.loc[rxn.id] - flux_rev.loc['rev_'+rxn.id]
    sol = flux
    sol.objective_value = SPOT.solution.get_objective_value()
    sol.status = SPOT.solution.get_status_string()
    
    # create a cobra solution object with flux values determined by cplex
    for r in model.reactions:
        r.upper_bound = sol[r.id]
        r.lower_bound = sol[r.id]
    solution = model.optimize()
    
    # confirm that cobra solution matches the cplex solution
    print(sol['EX_glc__D_e'])
    print(sol['Growth_Glucose'])
    print(solution.fluxes['EX_glc__D_e'])
    print(solution.fluxes['Growth_Glucose'])
    
    return solution

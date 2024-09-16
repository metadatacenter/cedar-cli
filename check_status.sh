#!/bin/bash
#
# check_status.sh [Microservice|Infrastructure|Frontend|Dashboard|Frontend-non-essential]
#
# Checks whether services in the given group are all running, in which case writes "OK". Otherwise, if at least
# one service fails, write "FAIL: ..." listing the services that fail.
#

shopt -s expand_aliases
alias cedarcli='source $CEDAR_HOME/cedar-cli/cli.sh'

if [[ $# -eq 0 ]] ; then
    echo 'Please provide the name of a service group as an argument.'
    exit 0
fi

GROUP=$1
FOUND_GROUP=false
ALL_OK=true
FAILING_SERVICES=""

while read -r line ; do
    if [[ $line == *"$GROUP"* ]] ; then
        FOUND_GROUP=true
        continue
    fi

    if [[ $FOUND_GROUP == true ]] ; then
        if [[ $line == *"├"* || $line == *"┡"* || $line == *"└"* ]] ; then
            break
        fi

        if [[ $line == *"❌"* ]] ; then
            ALL_OK=false
            FAILING_SERVICES+=$(echo $line | awk '{print $2}')" "
        fi
    fi
done < <(cedarcli server status)

if [[ $FOUND_GROUP == false ]] ; then
    echo "Invalid service group provided: $GROUP"
    exit 0
fi

if [[ $ALL_OK == true ]] ; then
    echo 'OK'
else
    echo "FAIL: $FAILING_SERVICES"
fi

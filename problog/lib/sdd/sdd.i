/* File: sdd.i */
%module sdd


%{
#define SWIG_FILE_WITH_INIT
#include "sddapi.h"
#include "parameters.h"
#include "compiler.h"
#include "array_access.h"
%}

%include "array_access.h"
%include "sddapi.h"

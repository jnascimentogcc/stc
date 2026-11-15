//COLLJOB  JOB (BANK),'COLLECTIONS DAILY',CLASS=A,MSGCLASS=X
//*--------------------------------------------------------------------
//* RUNS AFTER LNNIGHT
//*--------------------------------------------------------------------
//STEP010  EXEC PGM=COLLDUN
//STEPLIB  DD DSN=BANK.COLL.LOADLIB,DISP=SHR
//LATEIN   DD DSN=BANK.LOANS.LATE.SORTED,DISP=SHR
//DUNNOUT  DD DSN=BANK.COLL.DUNNING.DAILY,
//            DISP=(NEW,CATLG,DELETE),
//            SPACE=(CYL,(2,2),RLSE),
//            DCB=(RECFM=FB,LRECL=42)
//SYSOUT   DD SYSOUT=*
